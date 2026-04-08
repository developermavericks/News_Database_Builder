import { useState, useEffect } from "react";
import { api } from "../services/api";

export default function Diagnostics() {
    // PERSISTENCE FIX: Define helper first
    const checkSavedCooldown = () => {
        const savedCooldownEnd = localStorage.getItem('nexus_emergency_stop_end');
        if (savedCooldownEnd) {
            const remaining = Math.floor((parseInt(savedCooldownEnd) - Date.now()) / 1000);
            if (remaining > 0) {
                return { active: true, seconds: remaining };
            } else {
                localStorage.removeItem('nexus_emergency_stop_end');
            }
        }
        return { active: false, seconds: 0 };
    };

    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    
    // Initialize state directly from localStorage
    const status = checkSavedCooldown();
    const [cooldown, setCooldown] = useState(status.seconds);
    const [isCooldownActive, setIsCooldownActive] = useState(status.active);

    const fetchDiagnostics = async () => {
        try {
            const json = await api.get("/diagnostics/health");
            setData(json);
        } catch (e) {
            console.error(e);
            setData({
                overall: "offline",
                components: {
                    database: { status: "offline", message: "API Unreachable" },
                    groq_api: { status: "offline", message: "API Unreachable" },
                    playwright: { status: "offline", message: "API Unreachable" },
                    jobs: { status: "offline", message: "API Unreachable" }
                },
                recent_log_errors: ["Critical: Connection to Intelligence API lost."]
            });
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchDiagnostics();
        const interval = setInterval(fetchDiagnostics, 15000);
        return () => clearInterval(interval);
    }, []);

    // Re-check on every navigation mount to be absolutely sure
    useEffect(() => {
        const status = checkSavedCooldown();
        if (status.active) {
            setCooldown(status.seconds);
            setIsCooldownActive(true);
        }
    }, []);

    useEffect(() => {
        let timer;
        if (isCooldownActive && cooldown > 0) {
            timer = setInterval(() => {
                setCooldown(prev => {
                    const nextValue = prev - 1;
                    if (nextValue <= 0) {
                        localStorage.removeItem('nexus_emergency_stop_end');
                        setIsCooldownActive(false);
                        return 0;
                    }
                    return nextValue;
                });
            }, 1000);
        } else if (cooldown === 0) {
            setIsCooldownActive(false);
            localStorage.removeItem('nexus_emergency_stop_end');
        }
        return () => clearInterval(timer);
    }, [isCooldownActive, cooldown]);

    const formatTime = (seconds) => {
        const mins = Math.floor(seconds / 60);
        const secs = seconds % 60;
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    };

    const getStatusColor = (status) => {
        switch (status) {
            case "online": return "var(--success)";
            case "rate_limited": return "var(--warning)";
            case "degraded": return "var(--warning)";
            case "error": return "var(--danger)";
            case "offline": return "var(--danger)";
            default: return "var(--muted)";
        }
    };

    if (loading && !data) {
        return (
            <div style={{ textAlign: "center", padding: 100 }}>
                <div className="spinner" style={{ margin: "0 auto", width: 40, height: 40 }} />
                <p style={{ marginTop: 24, color: "var(--muted)", letterSpacing: '0.1em', fontSize: '11px', textTransform: 'uppercase' }}>Scanning System Integrity...</p>
            </div>
        );
    }

    const c = data?.components || {};

    const handleEmergencyStop = async () => {
        const warning = "⚠️ DANGER: This is a destructive action.\n\n" +
                      "This will PURGE all pending tasks and AGGRESSIVELY halt all active scrapes.\n" +
                      "It can crash current operations and cause data inconsistency.\n\n" +
                      "Are you ABSOLUTELY sure you want to proceed?";
        
        if (!window.confirm(warning)) return;

        const phrase = window.prompt("To authorize this action, please enter the authorization phrase:");
        if (!phrase) return;

        try {
            setLoading(true);
            const res = await api.post("diagnostics/emergency-stop", { phrase });
            
            // Initiate 5-minute cooldown (300 seconds)
            const endTime = Date.now() + (300 * 1000);
            localStorage.setItem('nexus_emergency_stop_end', endTime.toString());
            
            setCooldown(300);
            setIsCooldownActive(true);
            
            alert("Emergency Stop Triggered: " + (res.results?.actions?.join(", ") || "Success"));
            await fetchDiagnostics();
        } catch (e) {
            console.error(e);
            const errorDetail = e.response?.data?.detail || e.message || "Unknown Error";
            if (e.response?.status === 403) {
                alert("ACCESS DENIED: The authorization phrase was incorrect.");
            } else {
                alert(`Failed to trigger emergency stop: ${errorDetail}`);
            }
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="page-container">
            <header className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: '1px solid var(--border)', paddingBottom: '32px', marginBottom: '40px' }}>
                <div>
                    {/* <h1 className="page-title">System Telemetry</h1>
                    <p className="page-subtitle">Real-time health monitoring & component verification</p> */}
                </div>
                <div style={{ display: "flex", gap: "12px" }}>
                    {/* <button className="btn btn-secondary" onClick={fetchDiagnostics} disabled={loading} style={{ background: 'var(--surface)' }}>
                        {loading ? "Refreshing..." : "↻ Forced Sync"}
                    </button> */}
                    <button className="btn btn-danger" onClick={handleEmergencyStop} disabled={loading || isCooldownActive} style={{ 
                        background: isCooldownActive ? 'var(--muted)' : 'var(--danger)', 
                        color: 'white',
                        border: 'none',
                        boxShadow: isCooldownActive ? 'none' : '0 0 15px rgba(239, 68, 68, 0.4)',
                        cursor: isCooldownActive ? 'not-allowed' : 'pointer'
                    }}>
                        {loading ? "Processing..." : isCooldownActive ? "🛑 System Cooldown" : "🛑 Emergency Stop"}
                    </button>
                </div>
            </header>

            {isCooldownActive && (
                <div style={{
                    marginBottom: '40px',
                    padding: '40px',
                    borderRadius: 'var(--radius-lg)',
                    background: 'linear-gradient(135deg, #7f1d1d 0%, #450a0a 100%)',
                    border: '1px solid rgba(239, 68, 68, 0.3)',
                    textAlign: 'center',
                    boxShadow: '0 20px 40px rgba(0,0,0,0.4)',
                    animation: 'shake 0.5s ease-in-out'
                }}>
                    <div style={{ 
                        fontFamily: 'var(--font-mono)', 
                        fontSize: '12px', 
                        color: '#fca5a5', 
                        textTransform: 'uppercase', 
                        letterSpacing: '0.2em',
                        marginBottom: '16px'
                    }}>
                        System Quarantine Active • Cooldown in Progress
                    </div>
                    <div style={{ 
                        fontSize: '84px', 
                        fontWeight: '800', 
                        color: 'white', 
                        fontFamily: 'var(--font-mono)',
                        textShadow: '0 0 30px rgba(239, 68, 68, 0.5)',
                        lineHeight: '1'
                    }}>
                        {formatTime(cooldown)}
                    </div>
                    <div style={{ 
                        marginTop: '20px', 
                        fontSize: '14px', 
                        color: '#fca5a5',
                        maxWidth: '500px',
                        margin: '20px auto 0'
                    }}>
                        Intelligence streams have been forcefully severed. 
                        The global stop flag will expire in 5 minutes to ensure hardware safety and data integrity.
                    </div>
                </div>
            )}

            {/* <div style={{
                padding: "24px",
                marginBottom: 40,
                borderRadius: 'var(--radius)',
                background: 'var(--surface2)',
                borderLeft: `6px solid ${getStatusColor(data?.overall)}`,
                display: "flex",
                alignItems: "center",
                gap: 20,
                boxShadow: 'var(--glow)'
            }}>
                <div className="status-dot" style={{ background: getStatusColor(data?.overall), width: 14, height: 14 }} />
                <div style={{ fontSize: '20px', fontWeight: 600 }}>
                   NEXUS Status: <span style={{ color: getStatusColor(data?.overall), textTransform: "uppercase" }}>{data?.overall || "UNKNOWN"}</span>
                </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 24, marginBottom: 40 }}>
                <div className="card">
                    <div className="card-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        Core Database
                        <span className="badge" style={{ background: getStatusColor(c.database?.status) }}>{c.database?.status}</span>
                    </div>
                    <div style={{ fontSize: '13px', color: "var(--muted)", marginTop: '12px' }}>
                        Primary storage cluster status and connection health.
                    </div>
                </div>

                <div className="card">
                    <div className="card-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        NLP Intelligence
                        <span className="badge" style={{ background: getStatusColor(c.groq_api?.status) }}>{c.groq_api?.status}</span>
                    </div>
                    <div style={{ fontSize: '13px', color: "var(--muted)", marginTop: '12px', marginBottom: '20px' }}>
                        Groq API rate limits and token availability.
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, background: "var(--bg)", padding: 16, borderRadius: '8px' }}>
                        <div>
                            <div style={{ fontSize: 9, textTransform: "uppercase", opacity: 0.6 }}>Remaining</div>
                            <div style={{ fontFamily: "var(--font-mono)", fontSize: 13 }}>{c.groq_api?.remaining_reqs ?? "—"} reqs</div>
                        </div>
                        <div>
                            <div style={{ fontSize: 9, textTransform: "uppercase", opacity: 0.6 }}>Tokens</div>
                            <div style={{ fontFamily: "var(--font-mono)", fontSize: 13 }}>{c.groq_api?.remaining_tokens ?? "—"}</div>
                        </div>
                    </div>
                </div>

                <div className="card">
                    <div className="card-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        Extraction Grid
                        <span className="badge" style={{ background: getStatusColor(c.jobs?.status) }}>{c.jobs?.status}</span>
                    </div>
                    <div style={{ fontSize: '13px', color: "var(--muted)", marginTop: '12px' }}>
                        Active scraping workers and queuing latency.
                    </div>
                    <div style={{ marginTop: '16px', display: 'flex', gap: '20px' }}>
                        <div style={{ fontSize: '12px' }}>Active: <strong style={{ color: 'var(--accent)' }}>{c.jobs?.running_count || 0}</strong></div>
                        <div style={{ fontSize: '12px' }}>Yield: <strong style={{ color: 'var(--success)' }}>{data?.total_articles_collected || 0}</strong></div>
                    </div>
                </div>

                <div className="card">
                    <div className="card-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        Browsing Engine
                        <span className="badge" style={{ background: getStatusColor(c.playwright?.status) }}>{c.playwright?.status}</span>
                    </div>
                    <div style={{ fontSize: '13px', color: "var(--muted)", marginTop: '12px' }}>
                        Playwright headless services for high-fidelity extraction.
                    </div>
                </div>
            </div>

            <div className="card" style={{ background: '#1c1917', border: 'none' }}>
                <div className="card-title" style={{ color: 'var(--danger)', display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <span style={{ fontSize: '8px' }}>●</span> Intelligence Stream Logs
                </div>
                <div style={{
                    marginTop: '20px',
                    padding: '20px',
                    background: '#0c0a09',
                    borderRadius: '8px',
                    fontFamily: 'var(--font-mono)',
                    fontSize: '12px',
                    color: 'var(--muted)',
                    maxHeight: '300px',
                    overflowY: 'auto'
                }}>
                    {data?.recent_log_errors?.length > 0 ? (
                        data.recent_log_errors.map((err, i) => (
                            <div key={i} style={{ paddingBottom: '12px', marginBottom: '12px', borderBottom: '1px solid #292524', color: '#f87171' }}>
                                [{new Date().toLocaleTimeString()}] {err}
                            </div>
                        ))
                    ) : (
                        <div style={{ color: 'var(--success)', opacity: 0.8 }}>✓ All intelligence streams performing within parameters.</div>
                    )}
                </div>
            </div> */}
        </div>
    );

}
