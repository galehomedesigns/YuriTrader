const KrakenClient = require('./krakenClient');
const config = require('./config');

async function buyDip() {
  const kraken = new KrakenClient();
  const pair = 'XXBTZCAD';
  const type = 'buy';
  const ordertype = 'market';

  try {
    const balance = await kraken.getBalance();
    const availableCAD = parseFloat(balance.ZCAD || 0);

    if (availableCAD < 5) {
      console.log(`[Yuri] Balance too low to buy: $${availableCAD} CAD`);
      return;
    }

    const ticker = await kraken.getTicker(pair);
    const lastPrice = parseFloat(ticker[pair].c[0]);
    const volume = (availableCAD / lastPrice).toFixed(8);

    console.log(`[Yuri] Buying the dip: BUY ${volume} BTC @ ~$${lastPrice} CAD (Total ~$${availableCAD} CAD)`);

    const result = await kraken.addOrder(pair, type, ordertype, volume);
    console.log('--- Order Result ---');
    console.log(JSON.stringify(result, null, 2));

    if (result.txid) {
      console.log(`SUCCESS: Dip bought! TXID: ${result.txid[0]}`);
    }
  } catch (e) {
    console.error('FAILED: Trade execution error:', e.message);
  }
}

buyDip();
