import os
import sys
import django
from dotenv import load_dotenv

# Set up Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lipastellar.settings')
django.setup()

from payments import stellar_utils
from stellar_sdk import Keypair

def setup_master_account():
    print("--- LipaStellar Master Account Setup ---")
    
    # Load environment variables
    load_dotenv()
    
    try:
        master = stellar_utils.get_or_create_master_funding_account()
        print(f"Master Public Key: {master.public_key}")
        print(f"Master Secret Key: {master.secret}")
        print("\nIMPORTANT: Your Master Secret Key has been saved to the .env file.")
        
        print("\nChecking balances...")
        balances = stellar_utils.get_account_balances(master.public_key)
        xlm_balance = next((b['balance'] for b in balances if b['asset'] == 'XLM'), '0')
        
        print(f"XLM Balance: {xlm_balance}")
        
        if float(xlm_balance) < 10:
            print("\nMaster account has low XLM. Attempting to fund via Friendbot...")
            stellar_utils.fund_account(master.public_key)
            print("Done.")
        else:
            print("\nMaster account is ready with XLM.")
            
    except Exception as e:
        print(f"Error during setup: {e}")

if __name__ == "__main__":
    setup_master_account()
