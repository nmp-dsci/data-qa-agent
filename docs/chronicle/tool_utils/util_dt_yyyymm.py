
##########################################################
# enrich: Time
#  run from 'chroncile'
import pandas as pd
import numpy as np
import time, sys, os, math, json


with open('assets/util_dt_yyyymm.json') as rrr :
    util_yyyymm = pd.DataFrame(json.loads(rrr.read()))

util_yyyymm['dt_yyyymm'] = pd.to_datetime(util_yyyymm['dt_yyyymm'] ,utc=True) + pd.DateOffset(days=1)
util_yyyymm['month'] = util_yyyymm.dt_yyyymm.dt.strftime('%b')
util_yyyymm['month_int'] = util_yyyymm.dt_yyyymm.dt.strftime('%m').astype(int)
util_yyyymm['month_int_fy'] = util_yyyymm.month_int.apply(lambda x: x-6 if x >= 7 else x+6)
util_yyyymm['cy_qtr'] = np.ceil(util_yyyymm.month_int/3).astype(int)
util_yyyymm['fy_qtr'] = np.ceil(util_yyyymm.month_int_fy/3).astype(int)

util_yyyymm['dt_yyyymm'] = util_yyyymm.dt_yyyymm.dt.strftime('%Y-%m')

# YTD
# max_dt = profile_df.query(f'agg0_c == "{dt_col}"').agg0_v.max()
# curr_dt = util_yyyymm.query(f'{dt_col} == "{max_dt}"').iloc[0]
# util_yyyymm['ytd'] = (util_yyyymm.month_int_fy).apply(lambda x: 1 if x <= curr_dt.month_int_fy else 0 )
# util_yyyymm['year_fy_ytd'] = util_yyyymm['year_fy'] * util_yyyymm['ytd']

enrich_dt = util_yyyymm[['dt_yyyymm','year_cy','year_fy','month']]

# # Filter to actual dataset
# uniqValues = profile_df.query(f'agg0_c=="{dt_col}"').agg0_v.unique()
# enrich_dt = enrich_dt[enrich_dt[dt_col].isin(uniqValues)]

enrich_dt_json = enrich_dt.to_dict(orient='record')

with open(f'tool_utils/util_dt_yyyymm.json','w') as fct:
    fct.write("{id} = '{json}'".format(id=f'util_dt_yyyymm',json=json.dumps(enrich_dt_json)))

