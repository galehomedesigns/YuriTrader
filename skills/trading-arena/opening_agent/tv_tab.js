/**
 * tv_tab.js - shared CDP tab selection so the dedicated DATA tab (used for bar
 * reads) is never mistaken for the TRADING tab (where orders are staged).
 *
 * The data tab's CDP targetId is persisted by tv_bars_fetch.js. Order/position
 * tools call pickTradingTab() which returns a TradingView chart tab that is NOT
 * the data tab. If no data tab is tracked, it just returns the only chart tab.
 */
const fs = require("fs");
const DATA_TAB_FILE = "/home/tonygale/openclaw/skills/trading-arena/logs/tv_data_tab.json";

// The data tab is identified by BOTH a CDP targetId AND a random nonce that we
// stamp into the tab's window. The nonce lets us prove a tab is the one WE
// created — so we never adopt (or worse, drive setSymbol on) the user's trading
// tab just because it picked up the legacy __OPENING_DATA_TAB__ marker.
function readDataTab() {
  try {
    const o = JSON.parse(fs.readFileSync(DATA_TAB_FILE, "utf8"));
    return { targetId: o.targetId || null, nonce: o.nonce || null };
  } catch (e) { return { targetId: null, nonce: null }; }
}
function readDataTabId() { return readDataTab().targetId; }
function writeDataTab(id, nonce) {
  try { fs.writeFileSync(DATA_TAB_FILE, JSON.stringify({ targetId: id, nonce: nonce })); } catch (e) {}
}
function clearDataTab() { try { fs.unlinkSync(DATA_TAB_FILE); } catch (e) {} }

function isChart(t) { return t.type === "page" && t.url && t.url.includes("tradingview.com/chart"); }

// The trading tab = a TradingView chart tab that is NOT the tracked data tab.
function pickTradingTab(tabs) {
  const dataId = readDataTabId();
  return tabs.find(t => isChart(t) && t.id !== dataId) || tabs.find(t => isChart(t));
}

module.exports = { DATA_TAB_FILE, readDataTab, readDataTabId, writeDataTab, clearDataTab, isChart, pickTradingTab };
