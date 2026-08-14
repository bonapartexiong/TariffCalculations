import { useState } from 'react';
import Head from 'next/head';
import axios from 'axios';

// Defaults to same-origin so the site works on Netlify Functions or any
// reverse proxy without a separate CORS-enabled backend.
const API_URL = process.env.NEXT_PUBLIC_API_URL || '';

const MATCH_SOURCE_LABELS = {
  llm: 'LLM semantic match',
  llm_embeddings: 'LLM embeddings',
  lexical_tfidf: 'Lexical fallback',
  hts_code: 'HTS code lookup',
};

function money(value) {
  return Number(value || 0).toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
  });
}

export default function Home() {
  const [formData, setFormData] = useState({ description: '', value: '' });
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const response = await axios.post(
        `${API_URL}/v1/calculate`,
        {
          description: formData.description,
          value: parseFloat(formData.value),
        },
        { timeout: 30000 }
      );
      setResult(response.data);
    } catch (err) {
      if (err.response) {
        const data = err.response.data;
        if (err.response.status === 429) {
          setError('Rate limit exceeded. Please wait a moment and try again.');
        } else if (err.response.status === 404) {
          setError(
            (data.error || 'No match found') +
              ' - ' +
              (data.suggestion || 'Try a more specific description.')
          );
        } else {
          setError(data.error || `Server error (${err.response.status})`);
        }
      } else if (err.request) {
        setError('Unable to reach the server. Check your connection and try again.');
      } else {
        setError('An unexpected error occurred. Please try again.');
      }
    } finally {
      setLoading(false);
    }
  };

  const inputStyle = {
    padding: '0.75rem',
    border: '1px solid #e2e8f0',
    borderRadius: '8px',
    fontSize: '1rem',
    width: '100%',
    boxSizing: 'border-box',
    outline: 'none',
  };

  return (
    <>
      <Head>
        <title>Duty Calculator - Customs Tariff Estimation</title>
        <meta
          name="description"
          content="Estimate customs duties and fees for imported goods using LLM-powered product matching."
        />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </Head>

      <div
        style={{
          padding: '2rem 1rem',
          maxWidth: '680px',
          margin: '0 auto',
          fontFamily: "Segoe UI, Roboto, Helvetica Neue, sans-serif",
          color: '#1a202c',
        }}
      >
        <header style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <h1 style={{ fontSize: '2.25rem', margin: '0 0 0.5rem', color: '#2d3748' }}>
            Duty Calculator
          </h1>
          <p style={{ margin: 0, color: '#718096', fontSize: '1rem' }}>
            Estimate customs duties and fees for imported goods. Matching is powered
            by LLM embeddings with a lexical fallback.
          </p>
        </header>

        <form
          onSubmit={handleSubmit}
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '1.25rem',
            padding: '1.5rem',
            backgroundColor: '#ffffff',
            borderRadius: '12px',
            boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
          }}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <label style={{ fontWeight: '600', color: '#4a5568' }}>Description</label>
            <input
              type="text"
              value={formData.description}
              onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              required
              style={inputStyle}
              placeholder="e.g. Leather handbag, men's running shoes, laptop computer"
            />
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <label style={{ fontWeight: '600', color: '#4a5568' }}>Value (USD)</label>
            <input
              type="number"
              step="0.01"
              min="0.01"
              value={formData.value}
              onChange={(e) => setFormData({ ...formData, value: e.target.value })}
              required
              style={inputStyle}
              placeholder="500.00"
            />
          </div>

          <button
            type="submit"
            disabled={loading}
            style={{
              padding: '0.75rem 1.5rem',
              backgroundColor: loading ? '#a0c4e8' : '#2b6cb0',
              color: 'white',
              border: 'none',
              borderRadius: '8px',
              fontSize: '1rem',
              fontWeight: '600',
              cursor: loading ? 'not-allowed' : 'pointer',
              transition: 'background-color 0.2s',
            }}
          >
            {loading ? 'Calculating...' : 'Calculate Duty'}
          </button>
        </form>

        {error && (
          <div
            style={{
              marginTop: '1.5rem',
              padding: '1rem',
              backgroundColor: '#fff5f5',
              border: '1px solid #fc8181',
              borderRadius: '8px',
              color: '#c53030',
            }}
          >
            {error}
          </div>
        )}

        {result && (
          <div
            style={{
              marginTop: '1.5rem',
              padding: '1.5rem',
              backgroundColor: '#f7fafc',
              borderRadius: '12px',
              boxShadow: '0 4px 12px rgba(0,0,0,0.06)',
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                flexWrap: 'wrap',
                gap: '0.5rem',
                marginBottom: '1rem',
              }}
            >
              <h2 style={{ fontSize: '1.4rem', margin: 0, color: '#2d3748' }}>
                Calculation Results
              </h2>
              {result.match_source && (
                <span
                  style={{
                    padding: '0.25rem 0.6rem',
                    borderRadius: '999px',
                    backgroundColor: '#ebf4ff',
                    color: '#2b6cb0',
                    fontSize: '0.8rem',
                    fontWeight: '600',
                  }}
                >
                  {MATCH_SOURCE_LABELS[result.match_source] || result.match_source}
                </span>
              )}
            </div>

            <div style={{ display: 'grid', gap: '0.6rem', fontSize: '0.95rem' }}>
              <p style={{ margin: 0 }}>
                <strong>Matched:</strong> {result.matched_description}
              </p>
              {result.hts_number && (
                <p style={{ margin: 0 }}>
                  <strong>HTS Code:</strong> {result.hts_number}
                </p>
              )}
              {result.matched_group && (
                <p style={{ margin: 0, color: '#718096', fontSize: '0.85rem' }}>
                  {result.matched_group}
                </p>
              )}
              <p style={{ margin: 0 }}>
                <strong>Confidence:</strong> {(result.confidence * 100).toFixed(1)}%
              </p>
              <p style={{ margin: 0 }}>
                <strong>Tariff Rate:</strong> {(result.tariff * 100).toFixed(2)}%
              </p>
              <p style={{ margin: 0 }}>
                <strong>Custom Duty:</strong> {money(result.duty)}
              </p>
              <p style={{ margin: 0 }}>
                <strong>Merchandise Processing Fee:</strong> {money(result.merchandise_processing_fee)}
              </p>
              <p style={{ margin: 0 }}>
                <strong>Harbor Maintenance Fee:</strong> {money(result.harbor_maintenance_fee)}
              </p>
            </div>

            <div
              style={{
                marginTop: '1rem',
                paddingTop: '1rem',
                borderTop: '1px solid #e2e8f0',
              }}
            >
              <h3 style={{ fontSize: '1.2rem', margin: '0 0 0.5rem', color: '#2d3748' }}>
                Subtotal: {money(result.subtotal)}
              </h3>
              <p
                style={{
                  fontSize: '0.85rem',
                  color: '#718096',
                  lineHeight: '1.5',
                  margin: 0,
                }}
              >
                {result.footnote}
              </p>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
