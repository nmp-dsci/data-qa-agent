# 
# Generate final table 
# final_combos = 298
batch_size = 100#
batch_len = int(np.ceil(len(final_combos)*1.0 / batch_size))#
# 
agg_cols = ['agg'+str(x)+'_c' for x in range(0,len(final_combos[0]))]#
# 
property_cols = ['cutid'] +agg_cols + [x.replace('_c','_v') for x in agg_cols] + list(metric_cols)
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
            combo_df = prospect_df.groupby(combo_aggs)[metric_cols].sum().reset_index() 
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


