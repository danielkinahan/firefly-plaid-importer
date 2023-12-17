import plaid
from plaid.api import plaid_api
from plaid.model.transactions_sync_request import TransactionsSyncRequest
import requests
import json
import toml
import schedule
import time
import datetime
import logging

cursors = []


# Function to read credentials from config.toml file


def read_config():
    with open('config.toml', 'r') as file:
        config_file = toml.load(file)
        config = config_file.get('config', {})
        if not config:
            logging.error("Unable to read config")
        accounts = config_file.get('accounts', {})
        if not accounts:
            logging.error("Unable to read accounts config")
        return config, accounts

# Function to get new transactions from Plaid. On first run this will return all.
# For this to work with multiple access tokens, we have an array to store cursors for each token.


def plaid_sync_transactions(client, config):

    global cursors

    for i in range(len(config['plaid_access_tokens'])):
        if cursors[i]:
            request = TransactionsSyncRequest(
                access_token=config['plaid_access_tokens'][i],
                cursor=cursors[i]
            )
        else:
            request = TransactionsSyncRequest(
                access_token=config['plaid_access_tokens'][i],
            )

        response = client.transactions_sync(request)
        transactions = response['added']
        cursors[i] = response['next_cursor']

        # Get transactions from Plaid
        while (response['has_more']):
            request = TransactionsSyncRequest(
                access_token=config['plaid_access_tokens'][i],
                cursor=cursors[i]
            )
            response = client.transactions_sync(request)
            transactions += response['added']
            cursors[i] = response['next_cursor']

    return transactions


def firefly_get_existing_transactions(config, accounts):

    firefly_api_key = config['firefly_api_key']
    firefly_base_url = config['firefly_base_url']
    headers = {
        'Authorization': f'Bearer {firefly_api_key}',
        'Content-Type': 'application/json'
    }

    for account in accounts.values():
        url = f"{firefly_base_url}/api/v1/accounts/{account}/transactions"
        response = requests.get(url, headers=headers).json()
        transactions = response['data']
        while (response['links'].get('next')):
            response = requests.get(
                response['links']['next'], headers=headers).json()
            transactions += response['data']

    return transactions


def firefly_filter_for_transaction_ids(firefly_existing_transactions):
    firefly_ids = {firefly_transaction['attributes']['transactions'][0]['external_id']
                   for firefly_transaction in firefly_existing_transactions}
    return firefly_ids


def clean_transaction_account_name(config, name):
    for string in config['remove_strings']:
        name = name.replace(string, '')
    return name


def find_matching_transactions(firefly_existing_transactions, plaid_transaction):

    matching = []

    p_date = plaid_transaction['date']
    if plaid_transaction['amount'] > 0:
        p_amount = plaid_transaction['amount']
        p_type = 'deposit'
    else:
        p_amount = abs(plaid_transaction['amount'])
        p_type = 'withdrawal'

    for firefly_transaction in firefly_existing_transactions:
        f_date = datetime.datetime.fromisoformat(
            firefly_transaction['attributes']['transactions'][0]['date']).date()
        f_amount = float(
            firefly_transaction['attributes']['transactions'][0]['amount'])
        f_type = firefly_transaction['attributes']['transactions'][0]['type']

        if p_type == f_type and p_amount == f_amount and p_date == f_date:
            matching.append(
                firefly_transaction['attributes']['transactions'][0]['transaction_journal_id'])

    return matching


def update_existing_transaction_with_id(config, firefly_id, plaid_id):

    firefly_api_key = config['firefly_api_key']
    firefly_base_url = config['firefly_base_url']
    headers = {
        'Authorization': f'Bearer {firefly_api_key}',
        'Content-Type': 'application/json'
    }

    payload = {
        "external_id": plaid_id
    }

    logging.info(
        f"Firefly transaction {firefly_id} matches plaid transaction {plaid_id}")

    response = requests.put(
        f'{firefly_base_url}/api/v1/transactions/{firefly_id}', headers=headers, data=json.dumps(payload))
    if response.status_code == 200:
        logging.info(f"Transaction '{plaid_id}' updated successfully.")
    else:
        logging.error(
            f"Failed to update transaction '{plaid_id}'. Status code: {response.status_code}")


def match_transaction(config, transaction, firefly_existing_transactions):

    matching = find_matching_transactions(
        firefly_existing_transactions, transaction)
    if len(matching) == 1:
        update_existing_transaction_with_id(
            config, matching[0], transaction['transaction_id'])
        return True
    elif len(matching) > 1:
        logging.info("Multiple matches found. Not updating.")

    return False

# Function to insert transactions into Firefly III


def insert_transactions(config, accounts, plaid_transactions, firefly_existing_transactions):

    firefly_api_key = config['firefly_api_key']
    firefly_base_url = config['firefly_base_url']
    headers = {
        'Authorization': f'Bearer {firefly_api_key}',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }

    firefly_ids = firefly_filter_for_transaction_ids(
        firefly_existing_transactions)

    for transaction in plaid_transactions:

        other_account = clean_transaction_account_name(
            config, transaction['name'])

        if transaction['transaction_id'] in firefly_ids:
            logging.info(
                f"Transaction '{transaction['name']}' already exists in Firefly. Skipping insertion.")
            continue

        if transaction['account_id'] not in accounts.keys():
            continue

        if config['match_transactions']:
            if match_transaction(config, transaction, firefly_existing_transactions):
                continue

        payload = {
            'transactions': [{
                "date": transaction['date'].isoformat(),
                "description": transaction['name'],
                "external_id": transaction['transaction_id'],
                "currency_code": transaction['iso_currency_code'],
                "tags": transaction['category']
            }]
        }

        # Transaction amount has to be positive number
        if transaction['amount'] < 0:
            payload['transactions'][0].update({
                "type": "deposit",
                "amount": abs(transaction['amount']),
                "source_name": other_account,
                "destination_id": accounts[transaction['account_id']],
            })
        else:
            payload['transactions'][0].update({
                "type": "withdrawal",
                "amount": transaction['amount'],
                "source_id": accounts[transaction['account_id']],
                "destination_name": other_account,
            })

        response = requests.post(
            firefly_base_url + '/api/v1/transactions', headers=headers, data=json.dumps(payload))

        if response.status_code == 200:
            logging.info(
                f"Transaction '{transaction['name']}' inserted successfully.")
            firefly_ids.add(transaction['transaction_id'])
        else:
            logging.error(
                f"Failed to insert transaction '{transaction['name']}'. Status code: {response.status_code}")


def sync(config, accounts, client, firefly_existing_transactions):
    logging.info("Starting sync...")
    plaid_transactions = plaid_sync_transactions(client, config)
    insert_transactions(
        config, accounts, plaid_transactions, firefly_existing_transactions)


def main():

    logging.basicConfig()
    logging.root.setLevel(logging.INFO)

    logging.info("Starting Plaid to Firefly sync.")

    global cursors

    # Read credentials from config file
    config, accounts = read_config()

    cursors = [None] * len(config['plaid_access_tokens'])

    # Initialize Plaid client
    configuration = plaid.Configuration(
        host=plaid.Environment.Development,
        api_key={
            'clientId': config['plaid_client_id'],
            'secret': config['plaid_secret'],
        }
    )
    api_client = plaid.ApiClient(configuration)
    client = plaid_api.PlaidApi(api_client)

    firefly_existing_transactions = firefly_get_existing_transactions(
        config, accounts)

    sync(config, accounts, client, firefly_existing_transactions)

    # schedule.every(10).minutes.do(
    #     sync,
    #     config=config,
    #     config=config,
    #     client=client,
    #     firefly_existing_transactions=firefly_existing_transactions
    # )

    # while True:
    #     schedule.run_pending()
    #     time.sleep(1)


if __name__ == "__main__":
    main()
