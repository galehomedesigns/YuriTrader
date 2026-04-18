const KrakenClient = require('./krakenClient');
const config = require('./config');

async function checkBalance() {
  const kraken = new KrakenClient();
  try {
    const balances = await kraken.getBalance();
    console.log('--- Account Balances ---');
    for (const [asset, amount] of Object.entries(balances)) {
      if (parseFloat(amount) > 0) {
        console.log(`${asset}: ${amount}`);
      }
    }
  } catch (e) {
    console.error('Error fetching balance:', e.message);
  }
}

checkBalance();
