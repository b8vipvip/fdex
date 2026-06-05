import type { ReactNode } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { useEffect } from 'react';
import { useAuthStore } from './store/authStore';
import LoginPage from './pages/LoginPage';
import RegisterPage from './pages/RegisterPage';
import DashboardPage from './pages/DashboardPage';
import ProjectCreatePage from './pages/ProjectCreatePage';
import ProjectDetailPage from './pages/ProjectDetailPage';
import DocumentPage from './pages/DocumentPage';
import SettingsPage from './pages/SettingsPage';
function Guard({children}:{children:ReactNode}){const token=useAuthStore(s=>s.token);return token?children:<Navigate to="/login" replace/>}
export default function App(){const loadMe=useAuthStore(s=>s.loadMe);useEffect(()=>{loadMe().catch(()=>{})},[loadMe]);return <Routes><Route path="/login" element={<LoginPage/>}/><Route path="/register" element={<RegisterPage/>}/><Route path="/" element={<Guard><DashboardPage/></Guard>}/><Route path="/projects/new" element={<Guard><ProjectCreatePage/></Guard>}/><Route path="/projects/:id" element={<Guard><ProjectDetailPage/></Guard>}/><Route path="/reports/:id" element={<Guard><DocumentPage/></Guard>}/><Route path="/settings" element={<Guard><SettingsPage/></Guard>}/></Routes>}
