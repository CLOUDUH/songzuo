import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

type Session = { id:number; started_at:string; ended_at:string|null; duration_seconds:number; is_sedentary:boolean };
type SeriesItem = { label:string; seconds:number; sedentary_count:number };
type Overview = {
  status:{ occupied:boolean; camera_online:boolean; camera_error?:string; session_started_at:string|null; session_duration_seconds:number; confidence:number; last_check:string|null; next_reminder_seconds:number|null };
  today:{ total_seconds:number; sedentary_count:number; longest_seconds:number; session_count:number };
  threshold_minutes:number;
  sessions:Session[];
  series:SeriesItem[];
};
type Settings = {
  sedentary_minutes:number; leave_grace_seconds:number; sample_interval_seconds:number;
  daily_report_enabled:boolean; daily_report_time:string; weekly_report_enabled:boolean;
  weekly_report_day:number; weekly_report_time:string; bark_enabled:boolean; webhook_enabled:boolean;
  camera_offline_alert_enabled:boolean; camera_offline_minutes:number; camera_recovery_alert_enabled:boolean;
  reminder_title:string; reminder_body:string; roi_x:number; roi_y:number; roi_w:number; roi_h:number;
};
type CameraSettings = {
  stream_url:string; username:string; auth_type:"none"|"basic"|"digest"; verify_tls:boolean;
  password_configured:boolean; cookie_configured:boolean; online:boolean; error:string;
};

const demo:Overview = {
  status:{occupied:true,camera_online:true,camera_error:"",session_started_at:new Date(Date.now()-46*60000).toISOString(),session_duration_seconds:2760,confidence:.91,last_check:new Date().toISOString(),next_reminder_seconds:840},
  today:{total_seconds:15780,sedentary_count:2,longest_seconds:4980,session_count:4}, threshold_minutes:60,
  sessions:[
    {id:1,started_at:"2026-08-26T08:55:00+08:00",ended_at:"2026-08-26T09:18:00+08:00",duration_seconds:1380,is_sedentary:false},
    {id:2,started_at:"2026-08-26T10:55:00+08:00",ended_at:"2026-08-26T12:18:00+08:00",duration_seconds:4980,is_sedentary:true},
    {id:3,started_at:"2026-08-26T13:42:00+08:00",ended_at:"2026-08-26T14:24:00+08:00",duration_seconds:2520,is_sedentary:false},
    {id:4,started_at:"2026-08-26T15:06:00+08:00",ended_at:"2026-08-26T16:21:00+08:00",duration_seconds:4500,is_sedentary:true},
  ],
  series:[{label:"一",seconds:19080,sedentary_count:2},{label:"二",seconds:17160,sedentary_count:1},{label:"三",seconds:21120,sedentary_count:2},{label:"四",seconds:15780,sedentary_count:1},{label:"五",seconds:9960,sedentary_count:0},{label:"六",seconds:3480,sedentary_count:0},{label:"日",seconds:0,sedentary_count:0}],
};
const defaultSettings:Settings = {sedentary_minutes:60,leave_grace_seconds:180,sample_interval_seconds:3,daily_report_enabled:true,daily_report_time:"20:30",weekly_report_enabled:true,weekly_report_day:7,weekly_report_time:"20:35",bark_enabled:false,webhook_enabled:false,camera_offline_alert_enabled:true,camera_offline_minutes:3,camera_recovery_alert_enabled:true,reminder_title:"该起身活动啦",reminder_body:"你已经连续坐了 {duration}，走动几分钟吧。",roi_x:0.15,roi_y:0.15,roi_w:0.7,roi_h:0.8};
const defaultCamera:CameraSettings = {stream_url:"http://192.168.1.80:2345/",username:"",auth_type:"basic",verify_tls:true,password_configured:false,cookie_configured:false,online:false,error:"等待连接"};

const api = async <T,>(path:string, init?:RequestInit):Promise<T> => {
  const response = await fetch(path, {headers:{"Content-Type":"application/json",...(init?.headers||{})},...init});
  if (!response.ok) { const payload=await response.json().catch(()=>null); throw new Error(payload?.detail||`请求失败 (${response.status})`); }
  return response.json();
};
const hhmm = (iso:string|null) => iso ? new Intl.DateTimeFormat("zh-CN",{hour:"2-digit",minute:"2-digit",hour12:false}).format(new Date(iso)) : "—";
const duration = (seconds:number) => { const m=Math.max(0,Math.floor(seconds/60)); const h=Math.floor(m/60); return h ? `${h}小时${m%60 ? ` ${m%60}分` : ""}` : `${m}分钟`; };

export default function App() {
  const [data,setData]=useState<Overview>(demo); const [settings,setSettings]=useState<Settings>(defaultSettings);
  const [camera,setCamera]=useState<CameraSettings>(defaultCamera); const [cameraPassword,setCameraPassword]=useState(""); const [cameraCookie,setCameraCookie]=useState("");
  const [period,setPeriod]=useState<"day"|"week"|"month">("week"); const [openSettings,setOpenSettings]=useState(false);
  const [settingsTab,setSettingsTab]=useState<"monitor"|"camera">("monitor"); const [cameraSaving,setCameraSaving]=useState(false); const [cameraMessage,setCameraMessage]=useState("");
  const [demoMode,setDemoMode]=useState(true); const [toast,setToast]=useState("");
  const load=useCallback(async()=>{ try { const next=await api<Overview>(`/api/overview?period=${period}`); setData(next); setDemoMode(false); } catch { setDemoMode(true); } },[period]);
  useEffect(()=>{ void load(); const timer=setInterval(load,15000); return()=>clearInterval(timer); },[load]);
  useEffect(()=>{ if(!openSettings)return; api<Settings>("/api/settings").then(setSettings).catch(()=>{}); api<CameraSettings>("/api/camera/settings").then(setCamera).catch(()=>{}); },[openSettings]);
  const openPanel=(tab:"monitor"|"camera")=>{setSettingsTab(tab);setCameraMessage("");setOpenSettings(true);};
  const show=(message:string)=>{setToast(message);setTimeout(()=>setToast(""),2500);};
  const save=async(event:FormEvent)=>{event.preventDefault();try{await api("/api/settings",{method:"PUT",body:JSON.stringify(settings)});setOpenSettings(false);show("设置已保存");void load();}catch{show("保存失败，请检查服务状态");}};
  const testPush=async()=>{try{await api("/api/notifications/test",{method:"POST"});show("测试消息已发送");}catch{show("发送失败，请检查推送配置");}};
  const saveCamera=async(event:FormEvent)=>{event.preventDefault();setCameraSaving(true);setCameraMessage("正在验证地址、认证信息和 MJPEG 画面…");try{const next=await api<CameraSettings>("/api/camera/settings",{method:"PUT",body:JSON.stringify({stream_url:camera.stream_url,username:camera.username,password:cameraPassword||null,auth_type:camera.auth_type,cookie:cameraCookie||null,verify_tls:camera.verify_tls})});setCamera(next);setCameraPassword("");setCameraCookie("");setCameraMessage("连接验证成功，服务已使用新摄像头继续监测。");show("摄像头设置成功");void load();}catch(error){setCameraMessage(error instanceof Error?error.message:"摄像头连接验证失败");}finally{setCameraSaving(false);}};
  const dateLabel=useMemo(()=>new Intl.DateTimeFormat("zh-CN",{year:"numeric",month:"long",day:"numeric",weekday:"long"}).format(new Date()),[]);
  const current=Math.floor(data.status.session_duration_seconds/60); const progress=Math.min(100,current/data.threshold_minutes*100);
  const maxSeries=Math.max(...data.series.map(x=>x.seconds),21600);

  return <main className="app-shell">
    <aside className="sidebar"><div className="brand"><span className="brand-mark">坐</span><span>松坐</span></div>
      <nav aria-label="主导航"><a className="nav-item active" href="#overview"><span>⌂</span>概览</a><a className="nav-item" href="#sessions"><span>◷</span>坐姿记录</a><a className="nav-item" href="#trend"><span>↗</span>趋势统计</a><button className="nav-item" onClick={()=>openPanel("camera")}><span>◉</span>摄像头</button><button className="nav-item" onClick={()=>openPanel("monitor")}><span>⚙</span>设置</button></nav>
      <button className="camera-status" onClick={()=>openPanel("camera")}><span className={data.status.camera_online?"status-dot":"status-dot offline"}/><span><b>{data.status.camera_online?"摄像头在线":"摄像头离线"}</b><small>{data.status.camera_online&&data.status.last_check?`${hhmm(data.status.last_check)} 完成检测`:(data.status.camera_error||"点击设置摄像头")}</small></span></button>
    </aside>
    <section className="content" id="overview">
      <header className="topbar"><div><p className="eyebrow">{dateLabel}{demoMode?" · 演示数据":""}</p><h1>今天坐得怎么样？</h1></div><button className="settings-button" onClick={()=>openPanel("monitor")} aria-label="打开设置">⚙</button></header>
      <section className="hero-grid"><article className="live-card"><div className="live-head"><span className="live-pill"><i/>实时监测</span><span className="muted">座位区域 · 置信度 {Math.round(data.status.confidence*100)}%</span></div>
        <div className={`presence-visual ${data.status.occupied?"":"empty"}`}><span className="chair-back"/><span className="person-head"/><span className="person-body"/><span className="chair-seat"/></div>
        <div className="presence-copy"><span>当前状态</span><strong>{data.status.occupied?`已坐下 ${duration(data.status.session_duration_seconds)}`:"座位空闲"}</strong><small>{data.status.next_reminder_seconds!==null?`再坐 ${duration(data.status.next_reminder_seconds)}将提醒你起身`:"保持现在的活动节奏"}</small></div><div className="progress"><span style={{width:`${progress}%`}}/></div></article>
        <div className="metric-stack"><article className="metric-card warm"><span className="metric-icon">◷</span><div><small>今日坐姿时长</small><strong>{duration(data.today.total_seconds)}</strong><em>{data.today.session_count} 次坐下记录</em></div></article><article className="metric-card"><span className="metric-icon">!</span><div><small>今日久坐</small><strong>{data.today.sedentary_count} 次</strong><em className="neutral">阈值 {data.threshold_minutes} 分钟</em></div></article><article className="metric-card"><span className="metric-icon">↟</span><div><small>最长连续坐姿</small><strong>{duration(data.today.longest_seconds)}</strong><em className={data.today.longest_seconds>=data.threshold_minutes*60?"danger":""}>{data.today.longest_seconds>=data.threshold_minutes*60?"建议多休息":"状态不错"}</em></div></article></div>
      </section>
      <section className="panel" id="trend"><div className="panel-head"><div><h2>坐姿趋势</h2><p>工作与休息，找到自己的节奏</p></div><div className="segmented" aria-label="统计周期">{([['day','日'],['week','周'],['month','月']] as const).map(([value,label])=><button key={value} className={period===value?"selected":""} onClick={()=>setPeriod(value)}>{label}</button>)}</div></div>
        <div className="chart-wrap"><div className="y-labels"><span>6h</span><span>4h</span><span>2h</span><span>0</span></div><div className="bars" style={{gridTemplateColumns:`repeat(${data.series.length},minmax(6px,1fr))`}}>{data.series.map((item,index)=><div className="bar-column" key={`${item.label}-${index}`}><div className="bar-track"><span className={index===data.series.length-1?"today":""} style={{height:`${Math.max(item.seconds/maxSeries*100,2)}%`}} title={`${item.label}：${duration(item.seconds)}`}/></div><b>{item.label}</b></div>)}</div></div>
      </section>
      <section className="panel" id="sessions"><div className="panel-head"><div><h2>今日记录</h2><p>共坐下 {data.today.session_count} 次 · 久坐 {data.today.sedentary_count} 次</p></div><span className="muted">自动保留分析数据</span></div><div className="session-list">{data.sessions.length?data.sessions.map(s=><div className="session-row" key={s.id}><div className="time-line"><i/><span>{hhmm(s.started_at)}</span><span className="line"/><span>{hhmm(s.ended_at)}</span></div><strong>{duration(s.duration_seconds)}</strong><span className={s.is_sedentary?"badge long":"badge"}>{s.is_sedentary?"久坐":"正常"}</span></div>):<div className="empty-row">今天还没有坐姿记录</div>}</div></section>
    </section>
    {openSettings&&<div className="modal-backdrop" onMouseDown={()=>setOpenSettings(false)}><section className="settings-panel" onMouseDown={e=>e.stopPropagation()} role="dialog" aria-modal="true" aria-label="服务设置">
      <div className="panel-head"><div><p className="eyebrow">服务设置</p><h2>{settingsTab==="camera"?"摄像头连接":"监测与提醒"}</h2></div><button type="button" className="close-button" onClick={()=>setOpenSettings(false)}>×</button></div>
      <div className="settings-tabs"><button className={settingsTab==="monitor"?"selected":""} onClick={()=>setSettingsTab("monitor")}>监测与推送</button><button className={settingsTab==="camera"?"selected":""} onClick={()=>setSettingsTab("camera")}>摄像头</button></div>
      {settingsTab==="monitor"?<form onSubmit={save}>
        <label>久坐提醒阈值 <b>{settings.sedentary_minutes} 分钟</b><input type="range" min="30" max="180" step="5" value={settings.sedentary_minutes} onChange={e=>setSettings({...settings,sedentary_minutes:+e.target.value})}/></label>
        <label>离座结束延迟<select value={settings.leave_grace_seconds} onChange={e=>setSettings({...settings,leave_grace_seconds:+e.target.value})}><option value="60">1 分钟</option><option value="180">3 分钟</option><option value="300">5 分钟</option></select></label>
        <label>采样间隔<select value={settings.sample_interval_seconds} onChange={e=>setSettings({...settings,sample_interval_seconds:+e.target.value})}><option value="2">2 秒</option><option value="3">3 秒</option><option value="5">5 秒（更省资源）</option></select></label>
        <div className="settings-section"><span>消息渠道</span></div>
        <label className="toggle-row"><span>Bark 即时提醒<small>密钥仅通过环境变量配置</small></span><input type="checkbox" checked={settings.bark_enabled} onChange={e=>setSettings({...settings,bark_enabled:e.target.checked})}/></label>
        <label className="toggle-row"><span>通用 Webhook<small>地址仅通过环境变量配置</small></span><input type="checkbox" checked={settings.webhook_enabled} onChange={e=>setSettings({...settings,webhook_enabled:e.target.checked})}/></label>
        <label className="toggle-row"><span>摄像头离线提醒<small>持续离线后发送服务失效消息</small></span><input type="checkbox" checked={settings.camera_offline_alert_enabled} onChange={e=>setSettings({...settings,camera_offline_alert_enabled:e.target.checked})}/></label>
        <label>离线多久后提醒<select value={settings.camera_offline_minutes} onChange={e=>setSettings({...settings,camera_offline_minutes:+e.target.value})}><option value="1">1 分钟</option><option value="3">3 分钟</option><option value="5">5 分钟</option><option value="10">10 分钟</option><option value="30">30 分钟</option></select></label>
        <label className="toggle-row"><span>恢复上线通知<small>仅在已经发送过离线提醒后通知</small></span><input type="checkbox" checked={settings.camera_recovery_alert_enabled} onChange={e=>setSettings({...settings,camera_recovery_alert_enabled:e.target.checked})}/></label>
        <div className="settings-section"><span>定期汇总</span></div>
        <label className="toggle-row"><span>每日小结<small>每天 {settings.daily_report_time} 推送</small></span><input type="checkbox" checked={settings.daily_report_enabled} onChange={e=>setSettings({...settings,daily_report_enabled:e.target.checked})}/></label>
        <label>每日推送时间<input className="time-input" type="time" value={settings.daily_report_time} onChange={e=>setSettings({...settings,daily_report_time:e.target.value})}/></label>
        <label className="toggle-row"><span>每周报告<small>周日汇总一周数据</small></span><input type="checkbox" checked={settings.weekly_report_enabled} onChange={e=>setSettings({...settings,weekly_report_enabled:e.target.checked,weekly_report_day:7})}/></label>
        <label>每周推送时间<input className="time-input" type="time" value={settings.weekly_report_time} onChange={e=>setSettings({...settings,weekly_report_time:e.target.value})}/></label>
        <label>提醒标题<input className="text-input" value={settings.reminder_title} onChange={e=>setSettings({...settings,reminder_title:e.target.value})}/></label><label>提醒内容<textarea value={settings.reminder_body} onChange={e=>setSettings({...settings,reminder_body:e.target.value})}/></label>
        <div className="button-row"><button type="button" className="secondary-button" onClick={testPush}>测试推送</button><button className="primary-button">保存设置</button></div>
      </form>:<form className="camera-form" onSubmit={saveCamera}>
        <div className={`connection-banner ${camera.online?"online":"offline"}`}><span className={camera.online?"status-dot":"status-dot offline"}/><div><b>{camera.online?"当前摄像头在线":"当前摄像头离线"}</b><small>{camera.online?"监测服务正在接收画面":camera.error||"请填写并验证连接"}</small></div></div>
        <p className="privacy-note">新配置会先建立临时连接并确认收到有效画面。验证失败时不会保存，也不会中断当前服务。</p>
        <label className="field-label">MJPEG 流地址<input className="text-input" type="url" required value={camera.stream_url} onChange={e=>setCamera({...camera,stream_url:e.target.value})} placeholder="http://192.168.1.80:2345/"/></label>
        <label className="field-label">认证方式<select className="full-select" value={camera.auth_type} onChange={e=>setCamera({...camera,auth_type:e.target.value as CameraSettings["auth_type"]})}><option value="none">无需认证</option><option value="basic">Basic</option><option value="digest">Digest</option></select></label>
        <label className="field-label">账号<input className="text-input" autoComplete="username" disabled={camera.auth_type==="none"} value={camera.username} onChange={e=>setCamera({...camera,username:e.target.value})}/></label>
        <label className="field-label">密码<input className="text-input" type="password" autoComplete="new-password" disabled={camera.auth_type==="none"} value={cameraPassword} onChange={e=>setCameraPassword(e.target.value)} placeholder={camera.password_configured?"已安全保存；留空不修改":"输入摄像头密码"}/></label>
        <label className="field-label">登录 Cookie（可选）<textarea value={cameraCookie} onChange={e=>setCameraCookie(e.target.value)} placeholder={camera.cookie_configured?"已安全保存；留空不修改":"仅在网页登录设备需要时填写"}/></label>
        <label className="toggle-row"><span>验证 HTTPS 证书<small>HTTP 摄像头不受此选项影响</small></span><input type="checkbox" checked={camera.verify_tls} onChange={e=>setCamera({...camera,verify_tls:e.target.checked})}/></label>
        {cameraMessage&&<p className={cameraMessage.includes("成功")?"form-message success":"form-message"}>{cameraMessage}</p>}
        <button className="primary-button wide" disabled={cameraSaving}>{cameraSaving?"正在验证连接…":"测试并保存摄像头"}</button>
        <p className="security-note">账号、密码和 Cookie 不会显示在网页响应中；它们单独保存在数据目录的受限文件里。请只在可信局域网中使用此页面。</p>
      </form>}
    </section></div>}
    {toast&&<div className="toast">{toast}</div>}
  </main>;
}
