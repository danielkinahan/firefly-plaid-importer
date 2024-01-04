# Plaid to Firefly III Importer

An application to import transactions from Plaid to Firefly III. Written in Python 3.12 with no external dependancies for disk memory other than what's recorded in Firefly.

## Requirements

- docker-compose
- A Plaid account with development access (free)
- A Firefly III instance

## Getting started

1. Clone this repo to your server
2. Launch Plaid Quickstart in development mode
    1.  Login in to your bank accounts to get the access-tokens
3. ```cp example.config.toml config.toml```
4. Fill out values of your config
5. ```docker-compose up -d```

## Features

- Fetches transactions from Plaid.
- Checks for existing transactions in Firefly III.
- Inserts new transactions into Firefly III.

## Roadmap

- Double sided transactions
- Front-end in Flask
- Integration of Plaid quickstart with front-end
