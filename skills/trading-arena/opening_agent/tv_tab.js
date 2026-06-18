/**
 * tv_tab.js - shared CDP tab selection so the dedicated DATA tabs (used for bar
 * reads) are never mistaken for the TRADING tab (where orders are staged).
 *
 * Supports multiple parallel data tabs (tabIdx 0..N). Tab 0 uses the legacy
 * file path for backwards compatibility; tabs 1+ use indexed filenames.
 * Order/position tools call pickTradingTab() which returns a TradingView chart
 * tab that is NOT any data tab.
 */
const fs = require("fs");
const path = require("path");
const LOGS_DIR = "/home/tonygale/openclaw/skills/trading-arena/logs";
const DATA_TAB_FILE = path.join(LOGS_DIR, "tv_data_tab.json");

function _tabFile(tabIdx) {
  if (tabIdx === undefined || tabIdx === null || tabIdx === 0) return DATA_TAB_FILE;
  return path.join(LOGS_DIR, `tv_data_tab_${tabIdx}.json`);
}

function readDataTab(tabIdx) {
  try {
    const o = JSON.parse(fs.readFileSync(_tabFile(tabIdx), "utf8"));
    return { targetId: o.targetId || null, nonce: o.nonce || null };
  } catch (e) { return { targetId: null, nonce: null }; }
}
function readDataTabId() { return readDataTab(0).targetId; }
function writeDataTab(id, nonce, tabIdx) {
  try { fs.writeFileSync(_tabFile(tabIdx), JSON.stringify({ targetId: id, nonce: nonce })); } catch (e) {}
}
function clearDataTab() { try { fs.unlinkSync(DATA_TAB_FILE); } catch (e) {} }

/** Return all tracked data tab IDs (across all tab indices). */
function allDataTabIds() {
  const ids = new Set();
  try {
    const files = fs.readdirSync(LOGS_DIR).filter(f => f.startsWith("tv_data_tab") && f.endsWith(".json"));
    for (const f of files) {
      try {
        const o = JSON.parse(fs.readFileSync(path.join(LOGS_DIR, f), "utf8"));
        if (o.targetId) ids.add(o.targetId);
      } catch (e) {}
    }
  } catch (e) {}
  return ids;
}

function isChart(t) { return t.type === "page" && t.url && t.url.includes("tradingview.com/chart"); }

// The trading tab = a TradingView chart tab that is NOT any tracked data tab.
function pickTradingTab(tabs) {
  const dataIds = allDataTabIds();
  return tabs.find(t => isChart(t) && !dataIds.has(t.id)) || tabs.find(t => isChart(t));
}

module.exports = { DATA_TAB_FILE, readDataTab, readDataTabId, writeDataTab, clearDataTab, allDataTabIds, isChart, pickTradingTab };
