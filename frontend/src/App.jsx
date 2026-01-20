import React from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import HomePage from './pages/HomePage';
import ArbitragePage from './pages/ArbitragePage';
import ValueBetsPage from './pages/ValueBetsPage';
import BonusExtractionPage from './pages/BonusExtractionPage';
import ProfilesPage from './pages/ProfilesPage';
import BankrollPage from './pages/BankrollPage';

function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/arbitrage" element={<ArbitragePage />} />
          <Route path="/valuebets" element={<ValueBetsPage />} />
          <Route path="/bonus" element={<BonusExtractionPage />} />
          <Route path="/profiles" element={<ProfilesPage />} />
          <Route path="/bankroll" element={<BankrollPage />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}

export default App;
