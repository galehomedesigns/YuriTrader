const KrakenClient = require('./krakenClient');
const config = require('./config');

async function sellAll() {
  const kraken = new KrakenClient();
  const pair = 'XXBTZCAD';
  const type = 'sell';
  const ordertype = 'market';

  try {
    const balance = await kraken.getBalance();
    const volume = parseFloat(balance.XXBT || 0);

    if (volume <= 0) {
      console.log(`[Yuri] No BTC to sell.`);
      return;
    }

    console.log(`[Yuri] Selling all BTC holdings: SELL ${volume} BTC @ Market`);

    const result = await kraken.addOrder(pair, type, ordertype, volume.toString());
    console.log('--- Order Result ---');
    console.log(JSON.stringify(result, null, 2));

    if (result.txid) {
      console.log(`SUCCESS: BTC sold! TXID: ${result.txid[0]}`);
    }
  } catch (e) {
    console.error('FAILED: Sell execution error:', e.message);
  }
}

sellAll();
