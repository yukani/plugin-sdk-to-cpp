import re
from typing import Sequence

from pandas.core.frame import DataFrame
from args import OUTPUT_PATH, DEBUG_MODE

import numpy as np
from pandas import read_csv

from models.Function import FunctionType, Function

from args import DATABASE_PATH, DEBUG_MODE, DUMP_PROTOTYPES

# Extract function name from DemangledName column
name_re = re.compile(r'::(~?\w+)\(*?')


def extract_name_from_demangled(demangled_name : str):
    name = demangled_name.replace('__', '::')
    return name_re.search(name).group(1)

# https://stackoverflow.com/questions/363944
def first_of_list_or(list, default_value):
    return next(iter(list), default_value)

operator_re = re.compile(r'\boperator\s*\b')  
def dump_prototypes(class_name : str, df : DataFrame):

    class_prefix_re = re.compile(f'{class_name}(?:__|::)')
    def normalize(prot : str):
        prot = class_prefix_re.sub('', prot)
        prot = operator_re.sub('operator_', prot) # replace `operator new` with `operator_new` (add underscore basically)
        return prot.replace('~', 'Destructor_')

    with (OUTPUT_PATH / f'{class_name}_Prototypes.h').open(mode='w') as f:

        def to_file(fn):
            f.write(f'{fn["ret_type"]} {normalize(fn["demangled_name"])};\n')

        df.apply(to_file, axis=1)

#  Returns a tuple of (Non-Virtual, and Virtual[Sorted by vmt index]) functions belonging to this class
def extract(class_name: str):
    csv_path = DATABASE_PATH / 'plugin-sdk.out.functions.csv'
    if not csv_path.exists():
        raise FileNotFoundError('plugin-sdk.out.functions.csv not present. Try re-running IDA plugin-sdk exporter.')

    # Cols used and their respective (type, our name)
    cols = {
        '10us': (str, 'address'),
        'Name': (str, 'full_name'),
        'DemangledName': (str, 'demangled_name'),
        'CC': (str, 'cc'),
        'RetType': (str, 'ret_type'),
        'ParamTypes': (str, 'arg_types'),
        'ParamNames': (str, 'arg_names'),
        'VTIndex': (np.int16, 'vt_index'),
        #'Type': (str, 'prototype'),
        # 'Module': str,
        # 'IsConst': bool,
        # 'Refs': np.int16,
        # 'Comment': str,
        # 'Priority': bool,
        # 'ForceOverloaded': bool
    }

    csv_df : DataFrame = read_csv(csv_path, dtype={k: v[0] for k, v in cols.items()}, usecols=cols.keys(), engine='c', sep=',', na_values=[], keep_default_na=False)
    csv_df.rename(columns={k: v[1] for k, v in cols.items()}, inplace=True) # Rename columns in order for Function constructor to work

    # Filter to only contain class members
    csv_df = csv_df[csv_df['demangled_name'].str.match(f'{class_name}(?:__|::)')] 

    # Actual function name column
    csv_df['stripped_name'] = csv_df['demangled_name'].apply(lambda dn: extract_name_from_demangled(dn))

    # Filter out all dtors other than the real one
    # As there's another compiler generated dtor that has `deletingFlags` which just calles the original dtor
    # But having that causes us to mark the dtor as overloaded below
    csv_df = csv_df[~csv_df['stripped_name'].str.contains('~') | csv_df["arg_names"].str.endswith("this")]

    # Is overloaded column
    csv_df['is_overloaded'] = csv_df['stripped_name'].duplicated(keep=False)
    
    # Sort virtual functions[FunctionType.METHOD]. Important, otherwise 
    # their VMT indices won't wont match up with GTAs
    csv_df.sort_values(inplace=True, by='vt_index')
    csv_df.reset_index(inplace=True)

    if csv_df.empty:
        print(f"No functions found for class {class_name}")
        return

    if DEBUG_MODE:
        print(csv_df.to_string())

    if DUMP_PROTOTYPES:
        dump_prototypes(class_name, csv_df)

    # Create Function objects
    fns = csv_df.apply(
        lambda s: Function(class_name=class_name, **s),
        axis=1  # Apply on each row
    )
    
    # TODO Maybe do some checks here (Eg.: Check for duplicate destructors)

    # Get all functions by type as a py list
    def get_all_by_type(types : Sequence[FunctionType]):
        filtered_df = fns[fns.apply(lambda f: f.type in types)]
        return filtered_df.tolist()

    fns_by_type = {
        # There can be only one destructor, so this has to be a single item, not a list
        FunctionType.DTOR:      first_of_list_or(get_all_by_type((FunctionType.DTOR, FunctionType.DTOR_VIRTUAL)), None), 
    }

    for type in (FunctionType.CTOR, FunctionType.VIRTUAL, FunctionType.METHOD, FunctionType.STATIC):
        fns_by_type[type] = get_all_by_type((type, ))

    if DEBUG_MODE:
        for k, v in fns_by_type.items():
            if v:
                print(f"==> {k}")
                print(v)

    return fns_by_type
    
