create "ai_specs/s1_data_pipeline.md" this spec will detail the coding agent the entire data pipeline will follow ELT prcess: 
  1. EXTRACT:  raw data extracts, use `yfinance` python package for pulling sP500 stocks for end of day prices for last 10 years and Balance sheet and accounting statement 
  daily and only add data that is new for a SP500 stock ticker, so new end of day prices and financial reporting for new year if exists and not override existing data 
  unless explicited cached data is over written. 
  2. LOAD: with the raw csv outputs from EXTRACT which be loaded into a `Postgres` database with tables  under a schema called raw_finance 
  3. TRANSFROM: I want to use `DBT` to manage data transformation to enforce governance and auditability, help DBT so that scripts can be added to create downstream tables
  for the raw loaded tables to fuse data together  to create reporting / analytics layer in the future. 

I've added the code bases for each of the packages used in the data pipelines use the source code to build inform how to generate the code /functions to use. 
*  `yfinance`: opensrc/yfinance
*  `postgres`: opensrc/postgres
* `dbt`: opensrc/dtb

Key outputs: is for 1 ticker AAPL the whole pipeline runs for ETL  


Look for opportunities to improve the data pipeline for finance data for missing process / recommendations to improve 


   