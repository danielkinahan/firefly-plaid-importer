# Plaid to Firefly III Importer

This project is a script to import transactions from Plaid to Firefly III.

## Requirements

- Python 3.12 or higher
- A Plaid account with transactions
- A Firefly III account

## Installation

1. Clone this repository:

2. Install the required Python packages:

```

pip install -r requirements.txt

```

## Usage

1. Update the `config.toml` file with your Plaid and Firefly III details.
2. Run the script:

```

python import.py

```

## Features

- Fetches transactions from Plaid.
- Checks for existing transactions in Firefly III.
- Inserts new transactions into Firefly III.
