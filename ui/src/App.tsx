import { BrowserRouter, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/shell";
import Dashboard from "./routes/Dashboard";
import Logs from "./routes/Logs";
import Output from "./routes/Output";
import Settings from "./routes/Settings";
import Sources from "./routes/Sources";

export default function App() {
  return (
    <BrowserRouter>
      <AppShell>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/sources" element={<Sources />} />
          <Route path="/output" element={<Output />} />
          <Route path="/logs" element={<Logs />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  );
}
