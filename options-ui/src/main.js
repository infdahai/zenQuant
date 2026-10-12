import { mount } from 'svelte';
import App from './App.svelte';
import Paper from './Paper.svelte';
mount(location.pathname === '/paper' ? Paper : App, { target: document.getElementById('app') });
