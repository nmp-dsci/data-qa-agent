

import pandas as pd 
import json


poa2sa2_df = pd.read_csv('assets/postcode_2_sa2.csv')

# Dedup: Award postcode to SA2 most it share is in  
dedup_poa = poa2sa2_df.groupby('POSTCODE').RATIO.max().reset_index()
# poa2sa2_df.groupby('POSTCODE').RATIO.max().describe()


poa2sa2_df = poa2sa2_df.merge(dedup_poa,on=['POSTCODE','RATIO'],how='inner')

poa2sa2_df = poa2sa2_df[['POSTCODE','SA2_MAINCODE_2011']]
poa2sa2_df = poa2sa2_df.rename(columns={'SA2_MAINCODE_2011':'SA2_MAINCODE'})
poa2sa2_df = poa2sa2_df.apply(lambda x : x.astype(str), axis=0)

#####################################
## Enhance postcode to higher level geos

sa2_names = pd.read_csv('assets/SA2_2021_AUST.csv')

keep_cols = ['SA2_CODE_2021','SA2_NAME_2021',
'SA3_NAME_2021','SA4_NAME_2021','GCCSA_NAME_2021',
'STATE_NAME_2021']

sa2_names = sa2_names[keep_cols]

sa2_names.groupby('SA2_CODE_2021').cumcount().value_counts()
sa2_names = sa2_names.rename(columns={'SA2_CODE_2021':'SA2_MAINCODE_2011'})

sa2_names.columns = [x[0:-5] for x in sa2_names.columns] 



sa2_names = sa2_names.apply(lambda x: x.str.replace("'",""), axis=0)

poa2sa2_df = poa2sa2_df.merge(sa2_names,on='SA2_MAINCODE',how='left')


print('mISSING sa2s') 
print(poa2sa2_df.isnull().sum(axis=0) )


poa2sa2_df = poa2sa2_df.query('SA2_NAME == SA2_NAME')

poa2sa2_df = poa2sa2_df.rename(columns = {'POSTCODE':'postcode'})
poa2sa2_df = poa2sa2_df.drop(columns =['SA2_MAINCODE'])

poa2sa2_df.to_csv('tool_utils/util_postcode.csv',index=False)

poa2sa2_df_json = poa2sa2_df.to_dict(orient='records')

with open(f'tool_utils/util_postcode.json','w') as fct:
    fct.write("{id} = '{json}'".format(id=f'util_postcode',json=json.dumps(poa2sa2_df_json)))

