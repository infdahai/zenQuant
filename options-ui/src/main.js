import { mount } from 'svelte';
import App from './App.svelte';
import Paper from './Paper.svelte';
import Risk from './Risk.svelte';
mount(location.pathname === '/paper' ? Paper : location.pathname === '/risk' ? Risk : App, { target: document.getElementById('app') });
