import plaid
from plaid.api import plaid_api
from plaid.model.transactions_sync_request import TransactionsSyncRequest
import requests
import json
import toml
import schedule
import time
import datetime

cursor = ""

remove_strings = [
    'Visa Debit - Purchase - ',
    'Internet Deposit from Tangerine Chequing Account - ',
    'Internet Withdrawal to Tangerine Chequing Account - ',
    'Internet Deposit from Tangerine Savings Account - ',
    'Internet Withdrawal to Tangerine Savings Account - ',
    'Internet Deposit from TFSA TSA - ',
    'Internet Withdrawal to TFSA TSA - ',
    'INTERAC e-Transfer From: ',
    'INTERAC e-Transfer To: ',
    'Interac - Purchase - ',
    'EFT Deposit from ',
    'EFT Withdrawal to '
    'Deposit - ',
    'Withdrawal - ',
    'ABM - ',
    'Bill Payment - '
]

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


def firefly_get_existing_transactions(firefly_config):

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

    return transactions

def clean_transaction_account_name(name):
    for string in remove_strings:
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
        f_date = datetime.datetime.fromisoformat(firefly_transaction['attributes']['transactions'][0]['date'])
        f_amount = firefly_transaction['attributes']['transactions'][0]['amount']
        f_type = firefly_transaction['attributes']['transactions'][0]['type']

        # not quite working just yet
        if p_type == f_type and p_amount == f_amount and p_date == f_date.date():
            matching.append(firefly_transaction['attributes']['transactions'][0]['transaction_journal_id'])

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

    print(f"Firefly transaction {firefly_id} matches plaid transaction {plaid_id}")
    #response = requests.put(firefly_base_url + 'transactions/' + firefly_id, headers=headers, data=json.dumps(payload))

# Function to insert transactions into Firefly III
def insert_transactions(plaid_transactions, firefly_existing_transactions, firefly_config, plaid_config):

    firefly_api_key = firefly_config['api_key']
    firefly_base_url = firefly_config['base_url']
    headers = {
        'Authorization': f'Bearer {firefly_api_key}',
        'Content-Type': 'application/json'
    }

    firefly_ids = {firefly_transaction['attributes']['transactions'][0]['external_id'] for firefly_transaction in firefly_existing_transactions}

    for transaction in plaid_transactions:

        other_account = clean_transaction_account_name(transaction['name'])

        if transaction['transaction_id'] in firefly_ids:
            #print(f"Transaction '{description}' already exists in Firefly. Skipping insertion.")
            continue

        if transaction['account_id'] != plaid_config['account']:
            continue

        matching = find_matching_transactions(firefly_existing_transactions, transaction)
        if len(matching) == 1:
            update_existing_transaction_with_id(firefly_config, matching[0], transaction['transaction_id'])
            continue
        elif len(matching) > 1:
            print("Multiple matches found")

        # Transaction amount has to be positive number
        if transaction['amount'] > 0:
            payload = {
                "type": "deposit",
                "amount": transaction['amount'],           
                "date": transaction['date'],
                "description": transaction['name'],
                "source_name": other_account,
                "destination_id": plaid_config['account'],
                "external_id": transaction['transaction_id'],
                "currency_code": transaction['iso_currency_code'],
                "tags": transaction['category']
            }
        else:
            payload = {
                "type": "withdrawal",
                "amount": abs(transaction['amount']),           
                "date": transaction['date'],
                "description": transaction['name'],
                "source_id": plaid_config['account'],
                "destination_name": other_account,
                "external_id": transaction['transaction_id'],
                "currency_code": transaction['iso_currency_code'],
                "tags": transaction['category']
            }

        # response = requests.post(firefly_base_url + 'transactions', headers=headers, data=json.dumps(payload))

        # if response.status_code == 201:
        #     print(f"Transaction '{description}' inserted successfully.")
        #     existing_transactions_ids.add(plaid_transaction_id)
        # else:
        #     print(f"Failed to insert transaction '{description}'. Status code: {response.status_code}")

def loop(plaid_config, firefly_config, client, firefly_existing_transactions):
    plaid_transactions = plaid_sync_transactions(client, plaid_config)
    insert_transactions(plaid_transactions, firefly_existing_transactions, firefly_config, plaid_config)

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

    firefly_existing_transactions = firefly_get_existing_transactions(firefly_config)

    loop(plaid_config, firefly_config, client, firefly_existing_transactions)

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
