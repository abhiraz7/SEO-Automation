<?php
/**
 * Settings screen for VtechSEO Agent. All variables below ($token, $enabled,
 * $actions, $logs, $nonce, $api_base, $app_username, $has_yoast, $has_rankmath)
 * are set by VTSEO_Admin::render_page() just before this file is included.
 */
if ( ! defined( 'ABSPATH' ) ) exit;

$masked_token = $token ? substr( $token, 0, 8 ) . '…' . substr( $token, -8 ) : '';
$groups = [
    'content' => [ 'label' => 'Content',      'desc' => 'Create/update/delete posts &amp; pages, featured images, taxonomies.' ],
    'seo'     => [ 'label' => 'SEO (Yoast)',   'desc' => 'Read/write Yoast SEO meta, run audits, ping sitemaps.' ],
    'media'   => [ 'label' => 'Media',         'desc' => 'Upload/list/delete media, fix alt text (by ID or by URL).' ],
    'site'    => [ 'label' => 'Site info',     'desc' => 'Read-only site/plugin info, flush cache. No installs, no user or option writes.' ],
];
?>
<style>
  .vtseo-wrap{max-width:920px;margin:24px auto;color:#e5e5ea;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  .vtseo-wrap h1{font-size:22px;margin:0 0 4px;color:#fff}
  .vtseo-wrap .vtseo-sub{color:#9a9aa5;font-size:13px;margin:0 0 24px}
  .vtseo-card{background:#16161d;border:1px solid #2a2a35;border-radius:10px;padding:20px 22px;margin-bottom:18px}
  .vtseo-card h2{font-size:15px;margin:0 0 12px;color:#fff}
  .vtseo-row{display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap}
  .vtseo-code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#0f0f14;border:1px solid #2a2a35;border-radius:6px;padding:8px 10px;font-size:12.5px;color:#a8a8ff;word-break:break-all}
  .vtseo-btn{background:#4f46e5;color:#fff;border:none;border-radius:6px;padding:8px 14px;font-size:12.5px;font-weight:600;cursor:pointer}
  .vtseo-btn:hover{background:#4338ca}
  .vtseo-btn.ghost{background:transparent;border:1px solid #3a3a45;color:#c8c8d0}
  .vtseo-btn.ghost:hover{background:#22222c}
  .vtseo-btn.danger{background:transparent;border:1px solid #7f1d1d;color:#f87171}
  .vtseo-group{display:flex;align-items:flex-start;gap:10px;padding:10px 0;border-bottom:1px solid #22222c}
  .vtseo-group:last-child{border-bottom:none}
  .vtseo-group label{font-weight:600;color:#fff;font-size:13px}
  .vtseo-group p{margin:2px 0 0;color:#9a9aa5;font-size:12px}
  .vtseo-log{max-height:260px;overflow-y:auto;font-family:ui-monospace,monospace;font-size:11.5px}
  .vtseo-log-row{display:flex;gap:10px;padding:5px 0;border-bottom:1px solid #1c1c24}
  .vtseo-log-row .lvl-warn{color:#f59e0b}.vtseo-log-row .lvl-error{color:#f87171}.vtseo-log-row .lvl-info{color:#7dd3a8}
  .vtseo-toast{position:fixed;bottom:20px;right:20px;background:#1c1c24;border:1px solid #3a3a45;color:#fff;padding:10px 16px;border-radius:8px;font-size:13px;z-index:9999;display:none}
  .vtseo-badge{display:inline-block;padding:2px 8px;border-radius:99px;font-size:11px;font-weight:600}
  .vtseo-badge.on{background:#0f2a20;color:#33c288}
  .vtseo-badge.off{background:#2e1613;color:#ff6b5e}
</style>

<h1>🛰️ VtechSEO Agent</h1>
<p class="vtseo-sub">Lets the VtechSEO platform read on-page SEO data and apply approved fixes on this site. Scoped to content, SEO and media only — no page-builder control, no plugin installs, no PHP execution.</p>

<div class="vtseo-card">
  <h2>Connection</h2>
  <div class="vtseo-row">
    <span class="vtseo-badge <?php echo $enabled === '1' ? 'on' : 'off'; ?>"><?php echo $enabled === '1' ? 'Enabled' : 'Disabled'; ?></span>
    <label style="font-size:13px;color:#c8c8d0"><input type="checkbox" id="vtseo-enabled" <?php checked( $enabled, '1' ); ?>> Enabled</label>
  </div>
  <div class="vtseo-row"><strong style="font-size:12px;color:#9a9aa5;width:110px">API base URL</strong><span class="vtseo-code"><?php echo esc_html( $api_base ); ?></span></div>
  <div class="vtseo-row">
    <strong style="font-size:12px;color:#9a9aa5;width:110px">Bearer token</strong>
    <span class="vtseo-code" id="vtseo-token-display" data-token="<?php echo esc_attr( $token ); ?>"><?php echo esc_html( $masked_token ); ?></span>
    <button class="vtseo-btn ghost" id="vtseo-copy-token">Copy full token</button>
    <button class="vtseo-btn ghost" id="vtseo-regen-token">🔄 Regenerate</button>
  </div>
  <p style="font-size:11.5px;color:#7a7a85;margin:8px 0 0">Paste the API base URL and token into VtechSEO's WordPress connection screen. Regenerating immediately invalidates the old token everywhere it's used — do this any time you suspect it's been exposed.</p>
</div>

<div class="vtseo-card">
  <h2>Alternative: WordPress Application Password</h2>
  <p style="font-size:12px;color:#9a9aa5;margin:0 0 10px">WordPress's own built-in per-app credential system — revocable any time from <strong>Users → Profile</strong> without touching this plugin.</p>
  <?php if ( $app_username ): ?>
    <p style="font-size:12px;color:#c8c8d0" id="vtseo-app-username-label">WordPress user: <strong style="color:#fff"><?php echo esc_html( $app_username ); ?></strong> (password shown only once, at creation)</p>
  <?php else: ?>
    <p style="font-size:12px;color:#7a7a85" id="vtseo-no-app-pw">No Application Password created yet for this connection.</p>
  <?php endif; ?>
  <button class="vtseo-btn ghost" id="vtseo-create-app-pw">🔑 <?php echo $app_username ? 'Regenerate' : 'Create'; ?> Application Password</button>
  <div id="vtseo-app-pw-block" style="display:none;margin-top:10px">
    <div class="vtseo-row"><strong style="font-size:12px;color:#9a9aa5;width:80px">Password</strong><span class="vtseo-code" id="vtseo-app-pw-display"></span></div>
    <div class="vtseo-row"><strong style="font-size:12px;color:#9a9aa5;width:80px">Basic auth</strong><span class="vtseo-code" id="vtseo-app-encoded-display"></span></div>
    <p style="font-size:11px;color:#f59e0b">Copy this now — WordPress will not show it again.</p>
  </div>
</div>

<div class="vtseo-card">
  <h2>Enabled tool groups</h2>
  <?php foreach ( $groups as $key => $g ): ?>
    <div class="vtseo-group">
      <input type="checkbox" name="vtseo_actions[]" value="<?php echo esc_attr( $key ); ?>" <?php checked( in_array( $key, $actions, true ) ); ?> style="margin-top:3px">
      <div><label><?php echo esc_html( $g['label'] ); ?></label><p><?php echo $g['desc']; ?></p></div>
    </div>
  <?php endforeach; ?>
  <p style="font-size:11.5px;color:#7a7a85;margin:10px 0 0">
    Detected SEO plugin: <strong style="color:#c8c8d0"><?php echo $has_yoast ? 'Yoast SEO' : ( $has_rankmath ? 'RankMath (not yet supported by this plugin\'s SEO tools)' : 'none detected' ); ?></strong>
  </p>
  <button class="vtseo-btn" id="vtseo-save" style="margin-top:12px">Save settings</button>
</div>

<div class="vtseo-card">
  <h2>Recent activity</h2>
  <div class="vtseo-log">
    <?php if ( empty( $logs ) ): ?>
      <div style="text-align:center;padding:24px;color:#7a7a85;font-size:12px">No activity yet.</div>
    <?php else: foreach ( $logs as $entry ): ?>
      <div class="vtseo-log-row">
        <span style="color:#5a5a65;white-space:nowrap"><?php echo esc_html( $entry['time'] ); ?></span>
        <span class="lvl-<?php echo esc_attr( $entry['level'] ); ?>"><?php echo esc_html( strtoupper( $entry['level'] ) ); ?></span>
        <span style="color:#c8c8d0"><?php echo esc_html( $entry['message'] ); ?></span>
      </div>
    <?php endforeach; endif; ?>
  </div>
  <button class="vtseo-btn danger" id="vtseo-clear-logs" style="margin-top:12px">Clear logs</button>
</div>

<div class="vtseo-toast" id="vtseo-toast"></div>

<script>
(function(){
  const $ = (id) => document.getElementById(id);
  const nonce = <?php echo wp_json_encode( $nonce ); ?>;
  const ajaxurl = <?php echo wp_json_encode( admin_url( 'admin-ajax.php' ) ); ?>;

  function toast(msg, ok = true){
    const t = $('vtseo-toast');
    t.textContent = msg;
    t.style.display = 'block';
    t.style.borderColor = ok ? '#166534' : '#7f1d1d';
    setTimeout(() => { t.style.display = 'none'; }, 3500);
  }

  $('vtseo-copy-token')?.addEventListener('click', () => {
    const full = $('vtseo-token-display').dataset.token;
    navigator.clipboard.writeText(full).then(() => toast('Token copied.'));
  });

  $('vtseo-regen-token')?.addEventListener('click', () => {
    if (!confirm('Regenerate token? Any connection using the old token will need updating.')) return;
    fetch(ajaxurl, { method: 'POST', body: new URLSearchParams({ action: 'vtseo_regen', nonce }) })
      .then(r => r.json()).then(d => {
        if (d.success) {
          const t = d.data.token;
          const masked = t.length > 16 ? t.slice(0, 8) + '…' + t.slice(-8) : '•'.repeat(t.length);
          const el = $('vtseo-token-display');
          el.dataset.token = t;
          el.textContent = masked;
          toast('Token regenerated — update your VtechSEO connection.');
        }
      });
  });

  $('vtseo-create-app-pw')?.addEventListener('click', () => {
    const btn = $('vtseo-create-app-pw');
    btn.textContent = 'Creating…';
    btn.disabled = true;
    fetch(ajaxurl, { method: 'POST', body: new URLSearchParams({ action: 'vtseo_create_app_pw', nonce }) })
      .then(r => r.json()).then(d => {
        btn.disabled = false;
        if (d.success) {
          const { username, password, encoded } = d.data;
          const noMsg = $('vtseo-no-app-pw');
          if (noMsg) noMsg.style.display = 'none';
          const block = $('vtseo-app-pw-block');
          if (block) block.style.display = '';
          $('vtseo-app-pw-display').textContent = password;
          $('vtseo-app-encoded-display').textContent = 'Basic ' + encoded;
          btn.textContent = '🔄 Regenerate Application Password';
          toast("Application Password created! Copy it now — it won't be shown again.");
        } else {
          toast('Error: ' + (d.data?.message || 'Unknown error'), false);
        }
      }).catch(() => { btn.disabled = false; toast('Request failed.', false); });
  });

  $('vtseo-save')?.addEventListener('click', () => {
    const fd = new FormData();
    fd.append('action', 'vtseo_save');
    fd.append('nonce', nonce);
    if ($('vtseo-enabled').checked) fd.append('enabled', '1');
    fd.append('log_level', 'info');
    document.querySelectorAll('input[name="vtseo_actions[]"]:checked').forEach(c => fd.append('allowed_actions[]', c.value));
    fetch(ajaxurl, { method: 'POST', body: fd })
      .then(r => r.json()).then(d => toast(d.success ? 'Settings saved.' : 'Error saving.', d.success));
  });

  $('vtseo-clear-logs')?.addEventListener('click', () => {
    fetch(ajaxurl, { method: 'POST', body: new URLSearchParams({ action: 'vtseo_clear_logs', nonce }) })
      .then(r => r.json()).then(d => {
        if (d.success) {
          document.querySelector('.vtseo-log').innerHTML = '<div style="text-align:center;padding:24px;color:#7a7a85;font-size:12px">Log cleared.</div>';
          toast('Logs cleared.');
        }
      });
  });
})();
</script>
