
#  run from 'chroncile'
import pandas as pd
import numpy as np
import time, sys, os, math, json





##########################################################
# enrich: Location

# UTIL_SA4
sa4_col = 'loc_sa4'
util_sa4 = pd.read_csv('assets/sa4_names.csv')
util_sa4 = util_sa4.rename(columns = {'SA4_CODE_2016':sa4_col})
# util_sa4[sa4_col] = util_sa4[sa4_col].astype(float).astype(str)


enrich_sa4 = util_sa4[[sa4_col,'SA4_NAME_2016','GCCSA_CODE_2016',
    'STATE_NAME_2016','region_cat','region_subcat']]

# # Filter to actual dataset
# uniqValues = profile_df.query(f'agg0_c=="{sa4_col}"').agg0_v.astype(int).unique()
# enrich_sa4 = enrich_sa4[enrich_sa4[sa4_col].isin(uniqValues)]


json_dir = f'{file_dir}/datafeed'
enrich_sa4_json = enrich_sa4.to_dict(orient='record')

with open(f'tool_utils/util_{sa4_col}.json','w') as fct:
    fct.write("{id} = '{json}'".format(id=f'util_{sa4_col}',json=json.dumps(enrich_sa4_json)))


