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

cursor = ""


# Function to read credentials from config.toml file


def read_config():
    with open('config.toml', 'r') as file:
        config = toml.load(file)
        plaid_config = config.get('plaid', {})
        if not plaid_config:
            logging.error("Unable to read plaid config")
        firefly_config = config.get('firefly', {})
        if not firefly_config:
            logging.error("Unable to read firefly config")
        return plaid_config, firefly_config

# Function to get new transactions from Plaid. On first run this will return all.


def plaid_sync_transactions(client, plaid_config):

    global cursor

    if cursor:
        request = TransactionsSyncRequest(
            access_token=plaid_config['access_token'],
            cursor=cursor
        )
    else:
        request = TransactionsSyncRequest(
            access_token=plaid_config['access_token'],
        )

    response = client.transactions_sync(request)
    transactions = response['added']
    cursor = response['next_cursor']

    # Get transactions from Plaid
    while (response['has_more']):
        request = TransactionsSyncRequest(
            access_token=plaid_config['access_token'],
            cursor=cursor
        )
        response = client.transactions_sync(request)
        transactions += response['added']
        cursor = response['next_cursor']

    return transactions


def firefly_get_existing_transactions(firefly_config):

    firefly_api_key = firefly_config['api_key']
    firefly_base_url = firefly_config['base_url']
    headers = {
        'Authorization': f'Bearer {firefly_api_key}',
        'Content-Type': 'application/json'
    }

    url = firefly_base_url + '/api/v1/accounts/' + \
        firefly_config['account'] + '/transactions'
    response = requests.get(url, headers=headers).json()
    transactions = response['data']
    while (response['links'].get('next')):
        response = requests.get(
            response['links']['next'], headers=headers).json()
        transactions += response['data']

    return transactions


def clean_transaction_account_name(plaid_config, name):
    for string in plaid_config['remove_strings']:
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

        # not quite working just yet
        if p_type == f_type and p_amount == f_amount and p_date == f_date:
            matching.append(
                firefly_transaction['attributes']['transactions'][0]['transaction_journal_id'])

    return matching


def update_existing_transaction_with_id(firefly_config, firefly_id, plaid_id):

    firefly_api_key = firefly_config['api_key']
    firefly_base_url = firefly_config['base_url']
    headers = {
        'Authorization': f'Bearer {firefly_api_key}',
        'Content-Type': 'application/json'
    }

    payload = {
        "external_id": plaid_id
    }

    logging.info(
        f"Firefly transaction {firefly_id} matches plaid transaction {plaid_id}")

    response = requests.put(firefly_base_url + '/api/v1/transactions/' +
                            firefly_id, headers=headers, data=json.dumps(payload))
    if response.status_code == 200:
        logging.info(f"Transaction '{plaid_id}' updated successfully.")
    else:
        logging.error(
            f"Failed to update transaction '{plaid_id}'. Status code: {response.status_code}")

# Function to insert transactions into Firefly III


def insert_transactions(plaid_transactions, firefly_existing_transactions, firefly_config, plaid_config):

    firefly_api_key = firefly_config['api_key']
    firefly_base_url = firefly_config['base_url']
    headers = {
        'Authorization': f'Bearer {firefly_api_key}',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }

    firefly_ids = {firefly_transaction['attributes']['transactions'][0]['external_id']
                   for firefly_transaction in firefly_existing_transactions}

    for transaction in plaid_transactions:

        other_account = clean_transaction_account_name(
            plaid_config, transaction['name'])

        if transaction['transaction_id'] in firefly_ids:
            logging.info(
                f"Transaction '{transaction['name']}' already exists in Firefly. Skipping insertion.")
            continue

        if transaction['account_id'] != plaid_config['account']:
            continue

        payload = {
            'transactions': []
        }

        # Transaction amount has to be positive number
        if transaction['amount'] < 0:
            payload['transactions'].append({
                "type": "deposit",
                "amount": abs(transaction['amount']),
                "date": transaction['date'].isoformat(),
                "description": transaction['name'],
                "source_name": other_account,
                "destination_id": firefly_config['account'],
                "external_id": transaction['transaction_id'],
                "currency_code": transaction['iso_currency_code'],
                "tags": transaction['category']
            })
        else:
            payload['transactions'].append({
                "type": "withdrawal",
                "amount": transaction['amount'],
                "date": transaction['date'].isoformat(),
                "description": transaction['name'],
                "source_id": firefly_config['account'],
                "destination_name": other_account,
                "external_id": transaction['transaction_id'],
                "currency_code": transaction['iso_currency_code'],
                "tags": transaction['category']
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


def match_transactions(client, plaid_config, firefly_config, firefly_existing_transactions):
    # Code that updates matching transactions, to be run once
    firefly_ids = {firefly_transaction['attributes']['transactions'][0]['external_id']
                   for firefly_transaction in firefly_existing_transactions}
    plaid_transactions = plaid_sync_transactions(client, plaid_config)
    for transaction in plaid_transactions:
        if transaction['transaction_id'] in firefly_ids:
            continue

        matching = find_matching_transactions(
            firefly_existing_transactions, transaction)
        if len(matching) == 1:
            update_existing_transaction_with_id(
                firefly_config, matching[0], transaction['transaction_id'])
            continue
        elif len(matching) > 1:
            logging.info("Multiple matches found")


def sync(plaid_config, firefly_config, client, firefly_existing_transactions):
    plaid_transactions = plaid_sync_transactions(client, plaid_config)
    insert_transactions(
        plaid_transactions, firefly_existing_transactions, firefly_config, plaid_config)


def main():

    # Read credentials from config file
    plaid_config, firefly_config = read_config()

    # Initialize Plaid client
    configuration = plaid.Configuration(
        host=plaid.Environment.Development,
        api_key={
            'clientId': plaid_config['client_id'],
            'secret': plaid_config['secret'],
        }
    )
    api_client = plaid.ApiClient(configuration)
    client = plaid_api.PlaidApi(api_client)

    firefly_existing_transactions = firefly_get_existing_transactions(
        firefly_config)

    # match_transactions(client, plaid_config, firefly_config, firefly_existing_transactions)

    sync(plaid_config, firefly_config, client, firefly_existing_transactions)

    # schedule.every(10).minutes.do(
    #     sync,
    #     plaid_config=plaid_config,
    #     firefly_config=firefly_config,
    #     client=client,
    #     firefly_existing_transactions=firefly_existing_transactions
    # )

    # while True:
    #     schedule.run_pending()
    #     time.sleep(1)


if __name__ == "__main__":
    main()
