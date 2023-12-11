import plaid
from plaid.api import plaid_api
from plaid.model.transactions_sync_request import TransactionsSyncRequest
import requests
import json
import toml
import schedule
import time

cursor = ""

# Function to read credentials from config.toml file
def read_config():
    with open('config.toml', 'r') as file:
        config = toml.load(file)
        plaid_config = config.get('plaid', {})
        firefly_config = config.get('firefly', {})
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
    cursor=response['next_cursor']

    # Get transactions from Plaid
    while (response['has_more']):
        request = TransactionsSyncRequest(
            access_token=plaid_config['access_token'],
            cursor=cursor
        )
        response = client.transactions_sync(request)
        transactions += response['added']
        cursor=response['next_cursor']

    return transactions


def firefly_get_existing_transactions_external_ids(firefly_config):

    firefly_api_key = firefly_config['api_key']
    firefly_base_url = firefly_config['base_url']
    headers = {
        'Authorization': f'Bearer {firefly_api_key}',
        'Content-Type': 'application/json'
    }

    url = firefly_base_url + '/api/v1/accounts/' + firefly_config['account'] + '/transactions'
    response = requests.get(url, headers=headers).json()
    transactions = response['data']
    while(response['links'].get('next')):
        response = requests.get(response['links']['next'], headers=headers).json()
        transactions += response['data']

    ids = {transaction['attributes']['transactions'][0]['external_id'] for transaction in transactions}
    return ids

# Function to insert transactions into Firefly III
def insert_transactions(plaid_transactions, firefly_existing_transactions_ids, firefly_config):

    firefly_api_key = firefly_config['api_key']
    firefly_base_url = firefly_config['base_url']
    headers = {
        'Authorization': f'Bearer {firefly_api_key}',
        'Content-Type': 'application/json'
    }

    for transaction in plaid_transactions:
        amount = transaction['amount']
        date = transaction['date']
        description = transaction['name']
        plaid_transaction_id = transaction['transaction_id']

        if plaid_transaction_id in firefly_existing_transactions_ids:
            print(f"Transaction '{description}' already exists in Firefly. Skipping insertion.")
            continue

        payload = {
            "type": "withdrawal",
            "date": date,
            "description": description,
            "amount": amount,
            "external_id": plaid_transaction_id
        }

        # print(description)

        # response = requests.post(firefly_base_url + 'transactions', headers=headers, data=json.dumps(payload))

        # if response.status_code == 201:
        #     print(f"Transaction '{description}' inserted successfully.")
        #     existing_transactions_ids.add(plaid_transaction_id)
        # else:
        #     print(f"Failed to insert transaction '{description}'. Status code: {response.status_code}")

def loop(plaid_config, firefly_config, client, firefly_existing_transactions_ids):
    plaid_transactions = plaid_sync_transactions(client, plaid_config)
    insert_transactions(plaid_transactions, firefly_existing_transactions_ids, firefly_config)

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

    firefly_existing_transactions_external_ids = firefly_get_existing_transactions_external_ids(firefly_config)
    loop(plaid_config, firefly_config, client, firefly_existing_transactions_external_ids)

    # schedule.every(10).minutes.do(
    #     loop, 
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
