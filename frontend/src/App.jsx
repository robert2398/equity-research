import React, { useEffect, useState } from 'react';
import { fetchBundle } from './api.js';
import Overview from './tabs/Overview.jsx';
import Breadth from './tabs/Breadth.jsx';
import Sectors from './tabs/Sectors.jsx';
import Movers from './tabs/Movers.jsx';
import Detail from './tabs/Detail.jsx';
import Candidates from './tabs/Candidates.jsx';
import Replay from './tabs/Replay.jsx';
import RotationBacktest from './tabs/RotationBacktest.jsx';
import MoversBacktest from './tabs/MoversBacktest.jsx';
import BigMovers from './tabs/BigMovers.jsx';
import NextBullWatch from './tabs/NextBullWatch.jsx';
import SignalLedger from './tabs/SignalLedger.jsx';

const SECTIONS = [
  ['Live', [
    ['overview', 'Overview'], ['nbull', 'Next Bull Watch'], ['ledger', 'Signal Ledger'], ['breadth', 'Market Breadth'], ['sectors', 'Sectors'],
    ['movers', 'Top Movers'], ['detail', 'Stock Detail'], ['candidates', 'Candidates'],
  ]],
  ['Backtest', [
    ['replay', 'Week View'], ['rotation', 'Sector Rotation'], ['bt_movers', 'Top Movers'], ['big_movers', 'Big Movers'],
  ]],
];

export default function App() {
  const [tab, setTab] = useState('overview');
  const [bundle, setBundle] = useState(null);
  const [error, setError] = useState(null);
  const [focus, setFocus] = useState(null);   // symbol chosen anywhere -> Detail
  const [asOf, setAsOf] = useState(null);     // replay week context for Detail

  useEffect(() => {
    fetchBundle().then(setBundle).catch((e) => setError(e.message));
  }, []);

  const openStock = (symbol, asof = null) => {
    setFocus(symbol);
    setAsOf(asof);
    setTab('detail');
  };

  if (error) return <div className="loading">{error}</div>;
  if (!bundle) return <div className="loading">loading bundle…</div>;

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="logo">Bear-Market Reversal<small>NSE daily scanner</small></div>
        <div className="small" style={{ marginTop: 6 }}>
          data as of<br /><b>{bundle.as_of}</b>
        </div>
        <nav className="nav">
          {SECTIONS.map(([section, items]) => (
            <div key={section} style={{ marginBottom: 10 }}>
              <div className="small" style={{ padding: '0 12px 4px', textTransform: 'uppercase',
                letterSpacing: '0.6px', color: '#5f6b80' }}>{section}</div>
              {items.map(([id, label]) => (
                <button key={id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>
                  {label}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="small" style={{ marginTop: 'auto', position: 'absolute', bottom: 14, width: 170 }}>
          discovery tool · no orders<br />no profit claims
        </div>
      </aside>
      <main className="main">
        {tab === 'nbull' && <NextBullWatch onOpen={openStock} />}
        {tab === 'ledger' && <SignalLedger onOpen={openStock} />}
        {tab === 'overview' && <Overview bundle={bundle} onOpen={openStock} goTab={setTab} />}
        {tab === 'breadth' && <Breadth bundle={bundle} />}
        {tab === 'sectors' && <Sectors bundle={bundle} onOpen={openStock} />}
        {tab === 'movers' && <Movers bundle={bundle} onOpen={openStock} />}
        {tab === 'detail' && <Detail bundle={bundle} focus={focus} onOpen={openStock} asOf={asOf} clearAsOf={() => setAsOf(null)} />}
        {tab === 'candidates' && <Candidates bundle={bundle} onOpen={openStock} />}
        {tab === 'replay' && <Replay onOpen={openStock} />}
        {tab === 'rotation' && <RotationBacktest onOpen={openStock} />}
        {tab === 'bt_movers' && <MoversBacktest onOpen={openStock} />}
        {tab === 'big_movers' && <BigMovers onOpen={openStock} />}
      </main>
    </div>
  );
}
