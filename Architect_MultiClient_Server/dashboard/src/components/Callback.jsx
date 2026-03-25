import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import apiClient from '../services/api';
import LoadingSpinner from './LoadingSpinner';
import ErrorMessage from './ErrorMessage';

const Callback = () => {
  const [searchParams] = useSearchParams();
  const [error, setError] = useState(null);
  const { login } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    const handleCallback = async () => {
      try {
        // Get authorization code and state from URL
        const code = searchParams.get('code');
        const state = searchParams.get('state');

        if (!code || !state) {
          setError('Invalid callback: missing authorization code or state parameter.');
          return;
        }

        // Verify state matches what we stored (CSRF protection)
        const storedState = sessionStorage.getItem('oauth_state');

        if (!storedState || storedState !== state) {
          setError('Invalid state parameter. Possible CSRF attack. Please try logging in again.');
          sessionStorage.removeItem('oauth_state');
          return;
        }

        // Clear stored state
        sessionStorage.removeItem('oauth_state');

        // Exchange authorization code for JWT tokens
        const response = await apiClient.post('/api/auth/mezon/exchange', {
          code: code,
          state: state
        });

        const { access_token, refresh_token, user } = response.data;

        if (!access_token || !refresh_token || !user) {
          setError('Authentication failed: invalid response from server.');
          return;
        }

        // Store authentication in context and localStorage
        login(access_token, refresh_token, user);

        // Redirect to dashboard home
        navigate('/', { replace: true });

      } catch (err) {
        console.error('OAuth2 callback error:', err);

        let errorMessage = 'Authentication failed. Please try again.';

        if (err.response?.data?.detail) {
          errorMessage = err.response.data.detail;
        } else if (err.message) {
          errorMessage = err.message;
        }

        setError(errorMessage);
      }
    };

    handleCallback();
  }, [searchParams, login, navigate]);

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col justify-center py-12 sm:px-6 lg:px-8">
      <div className="sm:mx-auto sm:w-full sm:max-w-md">
        <div className="bg-white py-8 px-4 shadow sm:rounded-lg sm:px-10">
          {error ? (
            <div className="space-y-6">
              <ErrorMessage message={error} />

              <div className="text-center">
                <button
                  onClick={() => navigate('/login')}
                  className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500"
                >
                  Back to Login
                </button>
              </div>
            </div>
          ) : (
            <div className="space-y-6">
              <div className="flex justify-center">
                <LoadingSpinner size="large" />
              </div>

              <div className="text-center">
                <h2 className="text-xl font-semibold text-gray-900 mb-2">
                  Completing Authentication
                </h2>
                <p className="text-gray-600">
                  Please wait while we verify your credentials...
                </p>
              </div>

              <div className="bg-blue-50 border border-blue-200 rounded-md p-4">
                <div className="flex">
                  <div className="flex-shrink-0">
                    <svg
                      className="h-5 w-5 text-blue-400"
                      fill="currentColor"
                      viewBox="0 0 20 20"
                    >
                      <path
                        fillRule="evenodd"
                        d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z"
                        clipRule="evenodd"
                      />
                    </svg>
                  </div>
                  <div className="ml-3 flex-1">
                    <p className="text-sm text-blue-700">
                      You will be redirected to the dashboard automatically.
                    </p>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default Callback;
