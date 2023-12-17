import plaid
from plaid.api import plaid_api
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.model.accounts_get_request import AccountsGetRequest
import requests
import json
import toml
import schedule
import time
import logging

cursors = []


def read_config(config_filename):
    """
    Reads the configuration and account details from the config.toml file.

    Args:
        config_filename (str): The path to the config.toml file.
    Returns:
        tuple: A tuple containing two dictionaries. The first dictionary contains the configuration details,
               and the second dictionary contains the account details.
    """
    with open(config_filename, 'r') as file:
        config_file = toml.load(file)
        config = config_file.get('config', {})
        if not config:
            logging.error("Unable to read config")
        accounts = config_file.get('accounts', {})
        return config, accounts


def display_plaid_accounts(config, client):
    """
    Displays the accounts from Plaid for each access token

    Args:
        config (dict): The configuration details.
    """
    accounts = []

    for token in config['plaid_access_tokens']:
        request = AccountsGetRequest(access_token=token)
        response = client.accounts_get(request)
        accounts += response['accounts']

    if not accounts:
        logging.error("No accounts found in Plaid.")
        exit(1)
    print(accounts)


def plaid_sync_transactions(client, config):
    """
    Syncs transactions from Plaid. On the first run, this function will return all transactions.
    To handle multiple access tokens, this function uses a global list to store cursors for each token.

    Args:
        client (plaid.Client): The Plaid client.
        config (dict): The configuration details.
    Returns:
        list: A list of transactions from Plaid.
    """
    global cursors

    for i in range(len(config['plaid_access_tokens'])):
        # Use existing cursor if available
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


def firefly_get_transactions(config, accounts):
    """
    Fetches existing transactions from Firefly III.

    Args:
        config (dict): The configuration details.
        accounts (dict): The account details.

    Returns:
        list: A list of firefly transactions.
    """
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


def firefly_filter_for_transaction_ids(firefly_transactions):
    """
    Filters the existing transactions for transaction IDs.

    Args:
        firefly_transactions (list): The firefly transactions.

    Returns:
        firefly_ids: A set of transaction IDs.
    """
    firefly_ids = {firefly_transaction['attributes']['transactions'][0]['external_id']
                   for firefly_transaction in firefly_transactions}
    return firefly_ids


def clean_transaction_account_name(config, name):
    """
    Cleans the transaction account name by removing specified strings.

    Args:
        config (dict): The configuration details.
        name (str): The transaction account name.

    Returns:
        str: The cleaned transaction account name.
    """
    for string in config['remove_strings']:
        name = name.replace(string, '')
    return name


def find_matching_transactions(config, plaid_transaction):
    """
    Finds matching transactions between Firefly III and Plaid.

    Args:
        config (dict): The configuration details.
        plaid_transaction (dict): The transaction from Plaid.

    Returns:
        list: A list of matching transaction IDs.
    """

    p_date = plaid_transaction['date']
    if plaid_transaction['amount'] < 0:
        p_amount = abs(plaid_transaction['amount'])
        p_type = 'deposit'
    else:
        p_amount = plaid_transaction['amount']
        p_type = 'withdrawal'

    firefly_api_key = config['firefly_api_key']
    firefly_base_url = config['firefly_base_url']
    headers = {
        'Authorization': f'Bearer {firefly_api_key}',
        'Content-Type': 'application/json'
    }

    query = {
        "type": p_type,
        "amount": p_amount,
        "date_on": p_date.isoformat()
    }

    params = {
        "query": ' && '.join([f"{key}:{value}" for key, value in query.items()]),
    }

    url = f"{firefly_base_url}/api/v1/search/transactions"
    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        logging.error(
            f"Failed to get matching transactions. Status code: {response.status_code}")
        return []

    return response.json()['data']


def update_existing_transaction_with_id(config, firefly_id, plaid_id):
    """
    Updates an existing transaction in Firefly III with a Plaid transaction ID.

    Args:
        firefly_transaction (dict): The transaction from Firefly III.
        plaid_transaction_id (str): The ID of the transaction from Plaid.
    """
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


def match_transaction(config, transaction):
    """
    Matches a Plaid transaction with existing transactions in Firefly III.

    Args:
        config (dict): The configuration details.
        plaid_transaction (dict): The transaction from Plaid.

    Returns:
        boolean: True if one match is found and updated, False otherwise.
    """
    matching = find_matching_transactions(
        config, transaction)
    if len(matching) == 1:
        id = matching[0]['id']
        update_existing_transaction_with_id(
            config, id, transaction['transaction_id'])
        return True
    elif len(matching) > 1:
        logging.info("Multiple matches found. Not updating.")

    return False


def insert_transactions(config, accounts, plaid_transactions, firefly_ids):
    """
    Inserts new transactions into Firefly III.

    Args:
        config (dict): The configuration details.
        accounts (dict): The account details.
        plaid_transactions (list): The transactions from Plaid.
        firefly_ids (list): The existing external transaction ids from Firefly III.
    """
    firefly_api_key = config['firefly_api_key']
    firefly_base_url = config['firefly_base_url']
    headers = {
        'Authorization': f'Bearer {firefly_api_key}',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }

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
            if match_transaction(config, transaction):
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


def sync(config, accounts, client, firefly_ids):
    """
    Syncs transactions between Plaid and Firefly III.

    Args:
        config (dict): The configuration details.
        accounts (dict): The account details.
        client (plaid.Client): The Plaid client.
        firefly_ids (list): The existing transactions from Firefly III.
    """
    logging.info("Syncing...")
    plaid_transactions = plaid_sync_transactions(client, config)
    insert_transactions(
        config, accounts, plaid_transactions, firefly_ids)


def main():
    """
    The main function of the script. It reads the configuration, syncs transactions from Plaid,
    gets existing transactions from Firefly III, and creates new transactions in Firefly III.
    """
    logging.basicConfig()
    logging.root.setLevel(logging.INFO)

    global cursors

    # Read credentials from config file
    config, accounts = read_config('config.toml')

    logging.info("Connecting to Plaid.")
    configuration = plaid.Configuration(
        host=plaid.Environment.Development,
        api_key={
            'clientId': config['plaid_client_id'],
            'secret': config['plaid_secret'],
        }
    )
    api_client = plaid.ApiClient(configuration)
    client = plaid_api.PlaidApi(api_client)

    if not accounts:
        logging.warning(
            "No accounts found in config.toml. Displaying available accounts below:")
        display_plaid_accounts(config, client)
        exit(0)

    cursors = [None] * len(config['plaid_access_tokens'])

    logging.info("Getting transactions external_ids from Firefly.")
    firefly_ids = firefly_filter_for_transaction_ids(
        firefly_get_transactions(config, accounts))

    logging.info("Starting Plaid to Firefly sync.")
    sync(config, accounts, client, firefly_ids)

    # schedule.every(config['sync_minutes']).minutes.do(
    #     sync,
    #     config=config,
    #     accounts=accounts,
    #     client=client,
    #     firefly_ids=firefly_ids
    # )

    # while True:
    #     schedule.run_pending()
    #     time.sleep(1)


if __name__ == "__main__":
    main()
