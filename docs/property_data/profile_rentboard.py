# 
# Dependancies: Modelling table for "Acquisition Renewals" in "20200520_acq_renew"
#  Run locally
#
###

import os,sys ,json, shutil
import time,datetime,math
import pandas as pd
import numpy as np
# import matplotlib.pyplot as plt
import zlib, base64
import hashlib 

from tool_utils.util_gen_combos import generate_combos

# Get dATa

sourceid = 'rentboard'
fn = f'profile_{sourceid}'

home_dir = f'../data/propertyiq_getdata/{sourceid}'

outputdf_dir = f'{home_dir}/{sourceid}_df.csv'
base_df = pd.read_csv(outputdf_dir)


# postcode mapping
poa_map_df = pd.read_csv('tool_utils/util_postcode.csv')

# bedrooms
base_df.bedrooms = base_df.bedrooms.apply(lambda x: 'U' if x == 'U' else '5' if int(x) > 5 else x)

# prep columns
base_df['weekly_rent_raw']  = base_df['weekly_rent'] 
base_df['weekly_rent'] = pd.to_numeric(base_df['weekly_rent'],errors='coerce')

base_df['rent_yyyy'] = base_df['lodgement_dt'].astype(str).str.slice(0,4)
base_df['rent_yyyymm'] = base_df['lodgement_dt'].astype(str).str.slice(0,7) 


# metrics
base_df['rental_n_v']= 1
base_df['rent_n_v']= base_df['weekly_rent']

# filter dataset
base_df = base_df.query('postcode == postcode')
base_df = base_df.query('weekly_rent == weekly_rent')
# base_df = base_df.query('postcode in [2076,2095,2154,2077]')

# Filter for greater sydney 
# syd_poa = list(poa_map_df.query("GCCSA_NAME == 'Greater Sydney'")['postcode'].astype(str))
syd_poa = list(poa_map_df.query("SA3_NAME == 'Newcastle'")['postcode'].astype(str))
# syd_poa = syd_poa[:40]
base_df = base_df.query(f'postcode in [{",".join(list(syd_poa))}]')

base_df.postcode = base_df.postcode.astype(int).astype(str)

#########################################
#########################################
# Step 2
#########################################
#########################################
# Generate Combinations to visulise
dt_col = 'rent_yyyymm'#
agg_cols = [dt_col,'postcode','property_type','bedrooms']#
metrics = {'rental_n_v':np.sum, 'rent_n_v':np.sum} #

final_combos = generate_combos(agg_cols,dt_col)

####################################
# 
# Generate final table 
# final_combos = 298
batch_size = 100#
batch_len = int(np.ceil(len(final_combos)*1.0 / batch_size))#
# 
agg_cols = ['agg'+str(x)+'_c' for x in range(0,len(final_combos[0]))]#
# 
property_cols = ['cutid'] +agg_cols + [x.replace('_c','_v') for x in agg_cols] + list(metrics.keys())
profile_df = pd.DataFrame([], columns = property_cols)
# 
for bID, block in enumerate(range(0, batch_len)) : #
    print("Checking batch id: "+ str(bID))#
    block_min,block_max = block*100,  min([block*batch_size+(batch_size-1), len(final_combos)])#
    block_combos = final_combos[block_min:(block_max+1)]#
    ## check block already exists 
    base_exists = "profile_prop"+str(bID)  in list(globals().keys())#
    if base_exists == False:#
        print("Running batch id: "+ str(bID))#
        ## step 1 : generate the summary low level tables
        for idx, combo in enumerate(block_combos):#
            print(f"Running combo: {idx} ")#
            combo_aggs = [x  for x in combo if x != "'all'"]
            combo_df = base_df.groupby(combo_aggs).agg(metrics).reset_index() 
            # Agg prep 
            for cidx,col in enumerate(combo): 
                if col != "'all'": 
                    combo_df[f'agg{cidx}_c'] = col
                    combo_df = combo_df.rename(columns = {col:f'agg{cidx}_v'})
                else :
                    # Default missing 
                    combo_df[f'agg{cidx}_c'] = "all"
                    combo_df[f'agg{cidx}_v'] = "all"
            # Dump 
            combo_df['cutid'] = idx
            profile_df = pd.concat([profile_df,combo_df],axis=0)


#####################################
## Column identification

metrics = pd.Series(list(profile_df.columns))
metrics = metrics[metrics.str.contains('_n_v$')].to_list()

uniq_cols = list(np.setdiff1d(profile_df.columns,metrics))
profile_df = profile_df.sort_values(metrics[0], ascending=False)
profile_df = profile_df.fillna('missing').groupby(uniq_cols)[metrics].sum().reset_index() 

agg_cols = pd.Series(uniq_cols)
agg_cols = agg_cols[agg_cols.str.contains('agg\d_\w')].to_list()

search_cols = pd.Series(profile_df.columns)
search_cols = search_cols[search_cols.str.contains('agg\d_c')].to_list()

# Ensure all strings
profile_df[['cutid']+agg_cols] = profile_df[['cutid']+agg_cols].apply(lambda x: x.astype(str), axis=0)



################################
## Generate lookup DF 
lookup_df = profile_df.groupby(['cutid']+search_cols).size().reset_index()
lookup_df = lookup_df.drop(columns = [0])
assert lookup_df.cutid.value_counts().nunique() == 1, "ERROR: cutid is duping OUT "
# lookup_df[search_cols] = lookup_df[search_cols].astype(str)
lookup_df['value'] = lookup_df[search_cols].apply(lambda x: np.sort(x),axis=1)
lookup_df['value'] = lookup_df['value'].apply(lambda x: '___'.join(x))

lookup_json = lookup_df.to_dict(orient='records')

with open(f'datafeed/{fn}_lookup.json','w') as fct:
    fct.write("{id} = {json}".format(id='lookup_df',json=json.dumps(lookup_json)))

##################
## dump aggregated data 

## STEP 1: Write Dataset 
chart_base = profile_df.to_dict(orient='records')

with open(f'datafeed/{fn}.json','w') as fct:
    fct.write(f"data_df = {json.dumps(chart_base)}")
