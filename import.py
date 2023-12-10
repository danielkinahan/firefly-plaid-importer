import plaid
from plaid.api import plaid_api
from plaid.model.transactions_sync_request import TransactionsSyncRequest
import requests
import json
import toml

# Function to read credentials from config.toml file
def read_config():
    with open('config.toml', 'r') as file:
        config = toml.load(file)
        plaid_config = config.get('plaid', {})
        firefly_config = config.get('firefly', {})
        return plaid_config, firefly_config

# Function to retrieve transactions from Plaid
def get_transactions(plaid_client, access_token, start_date, end_date):
    transactions_response = plaid_client.Transactions.get(access_token, start_date=start_date, end_date=end_date)
    return transactions_response

# Function to insert transactions into Firefly III
def insert_transactions(transactions, firefly_api_key, firefly_base_url):
    headers = {
        'Authorization': f'Bearer {firefly_api_key}',
        'Content-Type': 'application/json'
    }

    for transaction in transactions['transactions']:
        amount = transaction['amount']
        date = transaction['date']
        description = transaction['name']

        payload = {
            "type": "withdrawal",
            "date": date,
            "description": description,
            "amount": amount
        }

        response = requests.post(firefly_base_url + 'transactions', headers=headers, data=json.dumps(payload))

        if response.status_code == 201:
            print(f"Transaction '{description}' inserted successfully.")
        else:
            print(f"Failed to insert transaction '{description}'. Status code: {response.status_code}")

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


    request = TransactionsSyncRequest(
        access_token=plaid_config['access_token'],
    )
    response = client.transactions_sync(request)
    transactions = response['added']

    # Get transactions from Plaid
    while (response['has_more']):
        request = TransactionsSyncRequest(
            access_token=plaid_config['access_token'],
            cursor=response['next_cursor']
        )
        response = client.transactions_sync(request)
        transactions += response['added']

    print(transactions)

    # Insert transactions into Firefly III
    # insert_transactions(plaid_transactions, firefly_config['api_key'], firefly_config['base_url'])

if __name__ == "__main__":
    main()