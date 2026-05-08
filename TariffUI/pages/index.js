import { useState } from 'react';
import Head from 'next/head';
import axios from 'axios';

const API_URL = process.env.NEXT_PUBLIC_API_URL || '';

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
        { timeout: 15000 }
      );
      setResult(response.data);
    } catch (err) {
      if (err.response) {
        // API returned a structured error
        const data = err.response.data;
        if (err.response.status === 429) {
          setError('Rate limit exceeded. Please wait a moment and try again.');
        } else if (err.response.status === 404) {
          setError(
            data.error + ' — ' + (data.suggestion || 'Try a more specific description.')
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
    borderRadius: '6px',
    fontSize: '1rem',
    width: '100%',
    boxSizing: 'border-box',
  };

  return (
    <>
      <Head>
        <title>Duty Calculator — Customs Tariff Estimation</title>
        <meta
          name="description"
          content="Estimate customs duties and fees for imported goods using product descriptions."
        />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </Head>

      <div
        style={{
          padding: '2rem',
          maxWidth: '600px',
          margin: '0 auto',
          fontFamily: "'Segoe UI', Roboto, 'Helvetica Neue', sans-serif",
        }}
      >
        <h1
          style={{
            fontSize: '2.5rem',
            marginBottom: '2rem',
            color: '#2d3748',
            textAlign: 'center',
          }}
        >
          Duty Calculator
        </h1>

        <form
          onSubmit={handleSubmit}
          style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <label
              style={{ fontSize: '1rem', fontWeight: '600', color: '#4a5568' }}
            >
              Description
            </label>
            <input
              type="text"
              value={formData.description}
              onChange={(e) =>
                setFormData({ ...formData, description: e.target.value })
              }
              required
              style={inputStyle}
              placeholder="e.g. Leather handbag"
            />
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <label
              style={{ fontSize: '1rem', fontWeight: '600', color: '#4a5568' }}
            >
              Value ($)
            </label>
            <input
              type="number"
              step="0.01"
              min="0.01"
              value={formData.value}
              onChange={(e) =>
                setFormData({ ...formData, value: e.target.value })
              }
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
              backgroundColor: loading ? '#a0c4e8' : '#4299e1',
              color: 'white',
              border: 'none',
              borderRadius: '6px',
              fontSize: '1rem',
              fontWeight: '600',
              cursor: loading ? 'not-allowed' : 'pointer',
              transition: 'background-color 0.2s',
              alignSelf: 'flex-start',
            }}
          >
            {loading ? 'Calculating…' : 'Calculate Duty'}
          </button>
        </form>

        {error && (
          <div
            style={{
              marginTop: '2rem',
              padding: '1rem',
              backgroundColor: '#fff5f5',
              border: '1px solid #fc8181',
              borderRadius: '6px',
              color: '#c53030',
            }}
          >
            {error}
          </div>
        )}

        {result && (
          <div
            style={{
              marginTop: '2.5rem',
              padding: '1.5rem',
              backgroundColor: '#f7fafc',
              borderRadius: '8px',
              boxShadow: '0 2px 4px rgba(0,0,0,0.05)',
            }}
          >
            <h2
              style={{
                fontSize: '1.5rem',
                marginBottom: '1rem',
                color: '#2d3748',
              }}
            >
              Calculation Results
            </h2>

            <div style={{ display: 'grid', gap: '0.75rem' }}>
              <p>
                <strong>Matched Description:</strong>{' '}
                {result.matched_description}
              </p>
              <p>
                <strong>Confidence:</strong>{' '}
                {(result.confidence * 100).toFixed(1)}%
              </p>
              <p>
                <strong>Tariff Rate:</strong>{' '}
                {(result.tariff * 100).toFixed(2)}%
              </p>
              <p>
                <strong>Custom Duty:</strong> ${result.duty.toFixed(2)}
              </p>
              <p>
                <strong>Merchandise Processing Fee:</strong> $
                {result.merchandise_processing_fee.toFixed(2)}
              </p>
              <p>
                <strong>Harbor Maintenance Fee:</strong> $
                {result.harbor_maintenance_fee.toFixed(2)}
              </p>
              <div
                style={{
                  marginTop: '1rem',
                  paddingTop: '1rem',
                  borderTop: '1px solid #e2e8f0',
                }}
              >
                <h3
                  style={{
                    fontSize: '1.25rem',
                    marginBottom: '0.5rem',
                    color: '#2d3748',
                  }}
                >
                  Subtotal: ${result.subtotal.toFixed(2)}
                </h3>
                <p
                  style={{
                    fontSize: '0.875rem',
                    color: '#718096',
                    lineHeight: '1.5',
                  }}
                >
                  {result.footnote}
                </p>
              </div>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
