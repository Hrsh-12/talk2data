"""Small pandas helpers for notebooks and ad-hoc CSV inspection."""

import pandas as pd


def load_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path)


def summarize(df: pd.DataFrame) -> None:
    print(f"Shape: {df.shape}")
    print(f"\nDtypes:\n{df.dtypes}")
    print(f"\nMissing values:\n{df.isnull().sum()}")
    print(f"\nDescribe:\n{df.describe()}")
