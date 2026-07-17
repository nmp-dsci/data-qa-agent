
import numpy as np



def generate_combos(agg_cols=[], multiLevels=[],levelsCombo=3, level4s = []):  
    """
    'agg_cols': generate all the 'agg_cols' string combinations. 
    'multiLevels': if going more that 1 level deep what key strings in 'agg_cols' do you want to keep (cant keep all)
    'levelCombo': how many - many combinations do you want to go e.g. 1 way , 2 way  , 3 way 
    'e

    """
    ############################################
    #  Generate all Combos 
    levelsAll = 4#
    #
    agg_combos = [[x] for x in agg_cols]#
    for lvl in range(1,levelsCombo): #
        print('level: '+ str(lvl))#
        agg_combos += [[y] + x for x in agg_combos for y in agg_cols if y not in x  ]#
    # 
    #sort and dedup
    [x.sort() for x in agg_combos]#
    agg_combos = ['_____'.join(x) for x in agg_combos ]#
    final_combos = [x.split('_____') for x in list(np.unique(agg_combos))]#
    print("1: Final length of combo's post deduping: "+ str(len(final_combos)))# 
    # 
    #################################
    # 4 way 
    if (len(level4s) > 0):#
        agg_combos3 = [x for x in final_combos if len(x) ==3 ]#
        agg_combs4 = []#
        for lvl4 in level4s:#
            print(lvl4)#
            agg_combs4 +=[[lvl4] + x for x in agg_combos3 if lvl4 not in x]#
        # 
        final_combos += agg_combs4#
    print("2: Final length of combo's post deduping: "+ str(len(final_combos)))# 
    #
    final_combos = [x for x in final_combos if (len(np.intersect1d(multiLevels,x)) > 0 or len(np.setdiff1d(x,["'all'"])) == 1) or multiLevels==[] ]
    print("3: Final length of combo's for DT filed only: "+ str(len(final_combos)))# 
    ## Dedup columns for agg and add dummy value for 
    final_combos = [x + list(np.repeat("'all'",levelsAll - len(x))) for x in final_combos]#
    #
    return final_combos

