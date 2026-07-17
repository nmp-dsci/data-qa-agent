# 
# Dependancies: 
# 
# 
# 
#
###

import os,sys ,json, shutil
import time,datetime,math
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import zlib, base64
import hashlib 

from tool_utils.util_gen_combos import generate_combos

pd.options.display.max_columns = 100 


# utility: postcode mapping
poa_map_df = pd.read_csv('../data/poa_2_sa4.csv')
poa_map_df.columns = poa_map_df.columns.str.lower() 
poa_map_df = poa_map_df.sort_values('ratio',ascending=False)
poa_map_df['dedup'] = poa_map_df.groupby('postcode').cumcount()
poa_map_df = poa_map_df.query('dedup == 0 ')
assert poa_map_df.postcode.value_counts().max() == 1 , "ERROR dups "




##########################################
# Get dATa
sourceid = 'nswgov'

home_dir = f'../data/propertyiq_getdata/{sourceid}'
output_dir = f'{home_dir}/output_etl3'

outputdf_dir = f'{output_dir}/{sourceid}_df.csv'
prop_df = pd.read_csv(outputdf_dir)

# prep columns
prop_df['sale_price'] = prop_df['sale_price'].astype(int)
prop_df['yyyy'] = prop_df['contract_dt'].astype(str).str.slice(0,4)
prop_df['yyyymm'] = prop_df['contract_dt'].astype(str).str.slice(0,4) +'-'+prop_df['contract_dt'].astype(str).str.slice(4,6)
prop_df['propertyType'] = prop_df['strata_no'].isnull().apply(lambda x: 'house' if x else 'unit')
prop_df['sold_n_v']= 1
prop_df['price_n_v']= prop_df['sale_price']
prop_df['postcode'] = prop_df.postcode.astype(int)

# filter dataset
prop_df = prop_df.query('sale_yyyy >= "2012" and sale_yyyy <= "2024"')
prop_df = prop_df.query('prop_purpose == "RESIDENCE"')
prop_df = prop_df.query('price_n_v <= 8000000')
prop_df = prop_df.query('postcode == postcode')


## avoca beach
# sub_sales_df = prop_df.query('postcode==2076')

# sub_sales_df = prop_df.query('yyyy >= "2016" and locality in ("HORNSBY","NORMANHURST","PADSTOW","SEVEN HILLS","ST MARYS")')
sub_sales_df = prop_df.query('yyyy >= "2016" and locality in ("MANLY")')
sub_sales_df = sub_sales_df.query('propertyType=="unit"') # house
# sub_sales_df = sub_sales_df[sub_sales_df.street_name.str.contains('PEATS')]
sub_sales_df = sub_sales_df.sort_values('price_n_v',ascending=False)
avg_sales_df = sub_sales_df.groupby(['yyyy','locality']).price_n_v.mean().unstack('locality')

avg_sales_df.plot(kind='line')
plt.show()


## avoca beach
# sub_sales_df = prop_df.query('postcode==2076')

sub_sales_df = prop_df.query('area_sqm > 1000 and yyyy >= "2016" and locality in ("HORNSBY","NORMANHURST","PADSTOW","SEVEN HILLS","ST MARYS")')
sub_sales_df = sub_sales_df.query('propertyType=="house"')
# sub_sales_df = sub_sales_df[sub_sales_df.street_name.str.contains('PEATS')]
sub_sales_df = sub_sales_df.sort_values('price_n_v',ascending=False)
avg_sales_df = sub_sales_df.groupby(['yyyy','locality']).price_n_v.mean().unstack('locality')

avg_sales_df.plot(kind='line')
plt.show()




# sub_sales_df[['house_no','settle_dt','sale_price','zoning','area_sqm']]

loan = 1200000
(loan*0.064)/12 + ((loan/(30*12))/2.5)


rent = 750
rent * 52/12 *0.95

#######################################
sourceid = 'rentboard'
fn = f'profile_{sourceid}'

home_dir = f'../data/propertyiq_getdata/{sourceid}'

outputdf_dir = f'{home_dir}/{sourceid}_df.csv'
rent_df = pd.read_csv(outputdf_dir)

# bedrooms
rent_df.bedrooms = rent_df.bedrooms.apply(lambda x: 'U' if x == 'U' else '5' if int(x) > 5 else x)

# prep columns
rent_df['weekly_rent_raw']  = rent_df['weekly_rent'] 
rent_df['weekly_rent'] = pd.to_numeric(rent_df['weekly_rent'],errors='coerce')
rent_df['yyyy'] = rent_df['lodgement_dt'].astype(str).str.slice(0,4)
rent_df['yyyymm'] = rent_df['lodgement_dt'].astype(str).str.slice(0,7) 
rent_df['postcode'] = rent_df.postcode.astype(int)
rent_df['propertyType'] = rent_df.property_type.apply(lambda x: 'house' if x in ('H','T') else 'unit')

# metrics
rent_df['rental_n_v']= 1
rent_df['rent_n_v']= rent_df['weekly_rent']

# filter dataset
rent_df = rent_df.query('postcode == postcode')
rent_df = rent_df.query('weekly_rent == weekly_rent')
# rent_df = rent_df.query('postcode in [2076,2095,2154,2077]')


# monthly avg rent
sub_rent_df = rent_df.query('postcode in (2077,2076,2147,2760) and propertyType =="house"')
avg_rent_df = sub_rent_df.groupby(['yyyy','postcode']).rent_n_v.mean().unstack('postcode')
avg_rent_df.plot(kind='line')
plt.show()


##
rent_df.query('property_type=="T"').postcode.value_counts()


###############################
## combine sales/ rent data for yield

agg_cols = ['yyyy','postcode','propertyType'] # yyyymm

## aggregate
pc_sales = prop_df.groupby(agg_cols).agg({'price_n_v':'mean','sold_n_v':'sum'}).reset_index()
pc_rent = rent_df.groupby(agg_cols).agg({'rent_n_v':'mean','rental_n_v':'sum'}).reset_index()

postcode_df = pc_sales.merge(pc_rent,on=agg_cols,how='outer')
postcode_df = postcode_df.query('yyyy >= "2016"' )

postcode_df = postcode_df.merge(poa_map_df[['postcode','sa4_name_2011']],on='postcode',how='left')

postcode_df.isnull().sum(axis=0)


# postcode_df = postcode_df.query('GCCSA_NAME in ("Greater Sydney","unknown")')

## Calculate yield
postcode_df['yield'] = (postcode_df.rent_n_v *52 )/ postcode_df.price_n_v


### TOP yields 

postcode_df = postcode_df[postcode_df.sa4_name_2011.str.contains('Sydney').fillna(False)]

exclude_sa4 = ['Sydney - Outer South West',
'Sydney - Outer West and Blue Mountains',
]


postcode_df.query(f'sa4_name_2011 not in {exclude_sa4} and yyyy=="2024" and propertyType=="house" and price_n_v <= 1500000 '
# postcode_df.query(f'yyyy=="2024" and propertyType=="house" and price_n_v <= 1300000 '
).sort_values('yield',ascending=False).head(20)


postcode_df.query('postcode==2077')



## Plot postcode 2077 Sales vs Rent
postcode_df.query('propertyType=="house" and postcode==2077'
    ).plot(x='yyyy',y=['price_n_v','rent_n_v','yield'],secondary_y='rent_n_v', subplots=True)
plt.show()

##3
postcode_df.query('propertyType=="house" and postcode==2760'
    ).plot(x='yyyy',y=['price_n_v','rent_n_v','yield'],secondary_y='rent_n_v', subplots=True)
plt.show()

# monthly avg rent
sub_rent_df = rent_df.query('postcode==2563')
avg_rent_df = sub_rent_df.groupby(['rent_yyyy']).rent_n_v.mean()
avg_rent_df.plot(kind='line')
plt.show()



# ABS Data 
# https://www.abs.gov.au/census/find-census-data/datapacks?release=2016&product=GCP&geography=ALL&header=S
