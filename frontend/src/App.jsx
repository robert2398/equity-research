import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
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
import IpoWatch from './tabs/IpoWatch.jsx';
import FnoAssistant from './tabs/FnoAssistant.jsx';

const SECTIONS = [
  ['Live', [
    ['fno', 'F&O Assistant'],
    ['overview', 'Overview'], ['nbull', 'Next Bull Watch'], ['ipo', 'IPO Watch'], ['ledger', 'Signal Ledger'], ['breadth', 'Market Breadth'], ['sectors', 'Sectors'],
    ['movers', 'Top Movers'], ['detail', 'Stock Detail'], ['candidates', 'Candidates'],
  ]],
  ['Backtest', [
    ['replay', 'Week View'], ['rotation', 'Sector Rotation'], ['bt_movers', 'Top Movers'], ['big_movers', 'Big Movers'],
  ]],
];
const LABEL = Object.fromEntries(SECTIONS.flatMap(([, items]) => items));

export default function App() {
  const [tab, setTab] = useState(() => window.location.hash === '#fno' ? 'fno' : 'overview');
  const [bundle, setBundle] = useState(null);
  const [error, setError] = useState(null);
  const [focus, setFocus] = useState(null);   // symbol chosen anywhere -> Detail
  const [asOf, setAsOf] = useState(null);     // replay week context for Detail
  const [from, setFrom] = useState(null);     // list tab the chart was opened from (kept mounted)
  const listScroll = useRef(0);
  const restoreScroll = useRef(null);
  const now = useRef({});
  now.current = { tab, from };

  useEffect(() => {
    fetchBundle().then(setBundle).catch((e) => setError(e.message));
  }, []);

  const openStock = (symbol, asof = null) => {
    setFocus(symbol);
    setAsOf(asof);
    if (tab !== 'detail') {                   // from a list: remember it and add a history step
      listScroll.current = window.scrollY;
      setFrom(tab);
      window.history.pushState({ chart: true }, '');
    }
    setTab('detail');
    window.scrollTo(0, 0);
  };

  // back to the list exactly as it was (filters, expanded rows, scroll)
  useEffect(() => {
    if ('scrollRestoration' in window.history) window.history.scrollRestoration = 'manual';
    const onPop = () => {
      const { tab: t, from: f } = now.current;
      if (t !== 'detail' || !f) return;
      restoreScroll.current = listScroll.current;
      setTab(f);
      setFrom(null);
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);
  useLayoutEffect(() => {
    if (restoreScroll.current != null && tab !== 'detail') {
      window.scrollTo(0, restoreScroll.current);
      restoreScroll.current = null;
    }
  }, [tab]);
  const goBack = () => window.history.back();    // -> popstate above (also the browser's Back)
  const goTab = (id) => {
    setFrom(null);
    setTab(id);
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}${id === 'fno' ? '#fno' : ''}`);
  };

  const page = (id) => {
    if (id !== 'fno' && !bundle) {
      return <div className="panel">
        <h1>{LABEL[id]}</h1>
        <p className="mut">{error || 'Loading daily scanner data...'}</p>
        <button type="button" className="btn" onClick={() => goTab('fno')}>Open F&O Assistant</button>
      </div>;
    }
    switch (id) {
      case 'fno': return <FnoAssistant />;
      case 'nbull': return <NextBullWatch onOpen={openStock} />;
      case 'ledger': return <SignalLedger onOpen={openStock} />;
      case 'ipo': return <IpoWatch onOpen={openStock} />;
      case 'overview': return <Overview bundle={bundle} onOpen={openStock} goTab={goTab} />;
      case 'breadth': return <Breadth bundle={bundle} />;
      case 'sectors': return <Sectors bundle={bundle} onOpen={openStock} />;
      case 'movers': return <Movers bundle={bundle} onOpen={openStock} />;
      case 'detail': return <Detail bundle={bundle} focus={focus} onOpen={openStock} asOf={asOf}
        clearAsOf={() => setAsOf(null)} back={from ? { label: LABEL[from], onClick: goBack } : null} />;
      case 'candidates': return <Candidates bundle={bundle} onOpen={openStock} />;
      case 'replay': return <Replay onOpen={openStock} />;
      case 'rotation': return <RotationBacktest onOpen={openStock} />;
      case 'bt_movers': return <MoversBacktest onOpen={openStock} />;
      case 'big_movers': return <BigMovers onOpen={openStock} />;
      default: return null;
    }
  };
  const mounted = tab === 'detail' && from ? [from, 'detail'] : [tab];

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="logo">Bear-Market Reversal<small>NSE daily scanner</small></div>
        <div className="small" style={{ marginTop: 6 }}>
          daily data as of<br /><b>{bundle?.as_of || (error ? 'Unavailable' : 'Loading...')}</b>
        </div>
        <nav className="nav">
          {SECTIONS.map(([section, items]) => (
            <div key={section} style={{ marginBottom: 10 }}>
              <div className="small" style={{ padding: '0 12px 4px', textTransform: 'uppercase',
                letterSpacing: '0.6px', color: '#5f6b80' }}>{section}</div>
              {items.map(([id, label]) => (
                <button key={id} className={tab === id ? 'active' : ''} onClick={() => goTab(id)}>
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
        {mounted.map((id) => (
          <div key={id} style={id === tab ? undefined : { display: 'none' }}>{page(id)}</div>
        ))}
      </main>
    </div>
  );
}
