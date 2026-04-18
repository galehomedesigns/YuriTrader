const KrakenClient = require('./krakenClient');
const config = require('./config');

async function makeTrade() {
  const kraken = new KrakenClient();
  const pair = 'XXBTZCAD';
  const type = 'buy';
  const ordertype = 'market';
  const amountCAD = 20.00;

  try {
    const ticker = await kraken.getTicker(pair);
    const lastPrice = parseFloat(ticker[pair].c[0]);
    const volume = (amountCAD / lastPrice).toFixed(8);

    console.log(`[Yuri] Placing market order: BUY ${volume} BTC @ ~$${lastPrice} CAD (Total ~$${amountCAD} CAD)`);

    const result = await kraken.addOrder(pair, type, ordertype, volume);
    console.log('--- Order Result ---');
    console.log(JSON.stringify(result, null, 2));

    if (result.txid) {
      console.log(`SUCCESS: Order placed! TXID: ${result.txid[0]}`);
    }
  } catch (e) {
    console.error('FAILED: Trade execution error:', e.message);
  }
}

makeTrade();
