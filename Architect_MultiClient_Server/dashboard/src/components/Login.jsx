import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import apiClient from '../services/api';
import LoadingSpinner from './LoadingSpinner';
import ErrorMessage from './ErrorMessage';

const Login = () => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const { isAuthenticated } = useAuth();
  const navigate = useNavigate();

  // Redirect to home if already authenticated
  useEffect(() => {
    if (isAuthenticated()) {
      navigate('/');
    }
  }, [isAuthenticated, navigate]);

  const generateRandomState = () => {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    let state = '';
    for (let i = 0; i < 11; i++) {
      state += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return state;
  };

  const handleLoginWithMezon = async () => {
    setLoading(true);
    setError(null);

    try {
      // Fetch OAuth2 configuration from backend
      const response = await apiClient.get('/api/v2/auth/mezon/config');
      const { client_id, auth_url, redirect_uri } = response.data;

      // Generate and store CSRF state
      const state = generateRandomState();
      sessionStorage.setItem('oauth_state', state);

      // Build authorization URL
      const params = new URLSearchParams({
        client_id: client_id,
        redirect_uri: redirect_uri,
        response_type: 'code',
        scope: 'openid',
        state: state
      });

      const authorizationUrl = `${auth_url}?${params.toString()}`;

      // Redirect to Mezon authorization page
      window.location.href = authorizationUrl;
    } catch (err) {
      console.error('Failed to initiate login:', err);
      setError(err.response?.data?.detail || 'Failed to connect to authentication service. Please try again.');
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col justify-center py-12 sm:px-6 lg:px-8">
      <div className="sm:mx-auto sm:w-full sm:max-w-md">
        <div className="text-center">
          <h1 className="text-4xl font-bold text-gray-900 mb-2">
            AI Agent Dashboard
          </h1>
          <p className="text-gray-600">
            Sign in to access your transcription dashboard
          </p>
        </div>
      </div>

      <div className="mt-8 sm:mx-auto sm:w-full sm:max-w-md">
        <div className="bg-white py-8 px-4 shadow sm:rounded-lg sm:px-10">
          {error && (
            <div className="mb-4">
              <ErrorMessage message={error} />
            </div>
          )}

          <div className="space-y-6">
            <button
              onClick={handleLoginWithMezon}
              disabled={loading}
              className="w-full flex justify-center items-center py-3 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 disabled:bg-indigo-400 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? (
                <>
                  <LoadingSpinner size="small" />
                  <span className="ml-2">Connecting...</span>
                </>
              ) : (
                <>
                  <svg
                    className="w-5 h-5 mr-2"
                    fill="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z" />
                  </svg>
                  Login with Mezon
                </>
              )}
            </button>

            <div className="mt-6">
              <div className="relative">
                <div className="absolute inset-0 flex items-center">
                  <div className="w-full border-t border-gray-300" />
                </div>
                <div className="relative flex justify-center text-sm">
                  <span className="px-2 bg-white text-gray-500">
                    Secure authentication via Mezon OAuth2
                  </span>
                </div>
              </div>
            </div>

            <div className="text-sm text-gray-600 text-center">
              <p>
                By signing in, you agree to authenticate using your Mezon account.
              </p>
            </div>
          </div>
        </div>

        <div className="mt-6 text-center text-sm text-gray-500">
          <p>
            Need help?{' '}
            <a href="https://mezon.ai/docs" className="text-indigo-600 hover:text-indigo-500">
              View documentation
            </a>
          </p>
        </div>
      </div>
    </div>
  );
};

export default Login;
