import { createContext, useContext, useState, useEffect } from 'react';
import apiClient from '../services/api';

const AuthContext = createContext(null);

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(null);
  const [loading, setLoading] = useState(true);

  // Initialize auth state from localStorage on mount
  useEffect(() => {
    const initAuth = async () => {
      const storedToken = localStorage.getItem('auth_token');

      if (storedToken) {
        setToken(storedToken);

        // Verify token and fetch user info
        try {
          const response = await apiClient.get('/api/auth/mezon/userinfo', {
            headers: {
              'Authorization': `Bearer ${storedToken}`
            }
          });

          if (response.data && response.data.user) {
            setUser(response.data.user);
          } else {
            // Invalid token, clear storage
            localStorage.removeItem('auth_token');
            setToken(null);
          }
        } catch (error) {
          console.error('Failed to verify token:', error);
          // Token might be expired or invalid
          localStorage.removeItem('auth_token');
          setToken(null);
        }
      }

      setLoading(false);
    };

    initAuth();
  }, []);

  const login = (jwtToken, userData) => {
    setToken(jwtToken);
    setUser(userData);
    localStorage.setItem('auth_token', jwtToken);
  };

  const logout = () => {
    setToken(null);
    setUser(null);
    localStorage.removeItem('auth_token');
  };

  const isAuthenticated = () => {
    return !!token && !!user;
  };

  const getAuthHeader = () => {
    if (token) {
      return { 'Authorization': `Bearer ${token}` };
    }
    return {};
  };

  const value = {
    user,
    token,
    loading,
    login,
    logout,
    isAuthenticated,
    getAuthHeader
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
};
