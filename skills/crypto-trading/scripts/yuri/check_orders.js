const KrakenClient = require('./krakenClient');

async function checkOrders() {
  const kraken = new KrakenClient();
  try {
    const orders = await kraken.getOpenOrders();
    console.log('--- Open Orders ---');
    console.log(JSON.stringify(orders, null, 2));

    const balance = await kraken.getBalance();
    console.log('--- Current Balance ---');
    console.log(JSON.stringify(balance, null, 2));
  } catch (e) {
    console.error('Error fetching orders:', e.message);
  }
}

checkOrders();
