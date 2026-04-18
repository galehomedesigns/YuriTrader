const KrakenClient = require('./krakenClient');
const config = require('./config');

async function checkStatus() {
  const kraken = new KrakenClient();
  const pair = 'XXBTZCAD';
  const holdings = 0.00021061;
  const buyPrice = 94964.00;

  try {
    const ticker = await kraken.getTicker(pair);
    const lastPrice = parseFloat(ticker[pair].c[0]);
    const currentVal = (holdings * lastPrice).toFixed(2);
    const pnl = ((lastPrice / buyPrice - 1) * 100).toFixed(2);

    console.log(`Current Price: $${lastPrice} CAD`);
    console.log(`Position Value: $${currentVal} CAD`);
    console.log(`P&L: ${pnl}%`);
  } catch (e) {
    console.error('Error fetching status:', e.message);
  }
}

checkStatus();
