"""Read patrol inputs from where the data lives: Snowflake, or the tables behind dbt models.

Every connector is read-only. It issues SELECT statements and nothing else, and returns the three
DataFrames a patrol needs: the fact table, the reference table and the source's control totals.
"""
