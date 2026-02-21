# LipaStellar - Merchant Payment Gateway (Username-based)

LipaStellar is a production-ready web application for Tanzanian merchants to accept payments via the Stellar blockchain. 

## Core Features

- **Merchant Onboarding:** Register with a unique username (e.g., `@mama_cafe`), get an automatically generated and funded Stellar wallet.
- **Merchant Dashboard:** View balances (XLM), track transaction history, and manage your payment link.
- **Customer Checkout:** Simple interface to pay merchants using their username. TZS amounts are converted to XLM and settled instantly on the Stellar Testnet.
- **Blockchain Transparency:** Every transaction includes a direct link to the Stellar Testnet Explorer.

## Technology Stack

- Python 3.10+
- Django 4.2+
- stellar-sdk
- cryptography (for secure key storage)
- Bootstrap 5
- SQLite

## Setup Instructions

1. **Clone the repository**
2. **Create a virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
4. **Master Account Setup (Optional):**
   The demo customer account is automatically funded with XLM by Friendbot. If you need a persistent funding account, you can use the setup script:
   ```bash
   python setup_master.py
   ```
   **Note:** XLM funding is usually handled automatically by the testnet Friendbot.
   
5. **Configure environment:**
   Copy `.env.example` to `.env` (or use the provided `.env`) and ensure `SECRET_KEY` is set.
6. **Run migrations:**
   ```bash
   python manage.py migrate
   ```
7. **Start the development server:**
   ```bash
   python manage.py runserver
   ```
8. **Access the app:**
   Open `http://127.0.0.1:8000` in your browser.

## How it Works

1. **Registration:** When a merchant registers, LipaStellar creates a new Stellar keypair and funds it via Friendbot.
2. **Payments:** Customers enter a merchant's username. The app looks up the merchant's public key, converts the TZS amount to XLM (at a fixed rate of 300 TZS/XLM), and executes a payment from a demo customer account.
3. **Verification:** Transactions are submitted to the Stellar Horizon server. Once confirmed, the transaction hash is stored, and a success page is shown with a live link to the blockchain explorer.

## Security

Merchant secret keys are encrypted using Fernet symmetric encryption (derived from the Django `SECRET_KEY`) before being stored in the database.

---
*Built for the Stellar Hackathon.*
