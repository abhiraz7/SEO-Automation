<?php
/**
 * Plugin Name: VtechSEO Agent
 * Plugin URI:  https://vtraffic.in
 * Description: Lets the VtechSEO platform read on-page SEO data and apply approved fixes (meta tags, image alt text, content) on this site. Scoped to content/SEO/media only -- no page-builder control, no plugin management, no raw PHP execution.
 * Version:     1.0.0
 * Author:      Vtechys
 * License:     GPL-2.0+
 * Text Domain: vtechseo-agent
 */

if ( ! defined( 'ABSPATH' ) ) exit;

define( 'VTSEO_VERSION',    '1.0.0' );
define( 'VTSEO_PLUGIN_DIR', plugin_dir_path( __FILE__ ) );
define( 'VTSEO_PLUGIN_URL', plugin_dir_url( __FILE__ ) );
define( 'VTSEO_SLUG',       'vtechseo-agent' );

/* ── Autoload modules ─────────────────────────────────────── */
require_once VTSEO_PLUGIN_DIR . 'includes/class-auth.php';
require_once VTSEO_PLUGIN_DIR . 'includes/class-router.php';
require_once VTSEO_PLUGIN_DIR . 'includes/class-mcp.php';
require_once VTSEO_PLUGIN_DIR . 'includes/class-logger.php';
require_once VTSEO_PLUGIN_DIR . 'mcp-handlers/handler-content.php';
require_once VTSEO_PLUGIN_DIR . 'mcp-handlers/handler-seo.php';
require_once VTSEO_PLUGIN_DIR . 'mcp-handlers/handler-media.php';
require_once VTSEO_PLUGIN_DIR . 'mcp-handlers/handler-site.php';
require_once VTSEO_PLUGIN_DIR . 'admin/class-admin.php';

/* ── Boot ─────────────────────────────────────────────────── */
add_action( 'plugins_loaded', function () {
    VTSEO_Auth::init();
    VTSEO_Router::init();
    VTSEO_Admin::init();
} );

/* ── REST namespace registration ─────────────────────────── */
add_action( 'rest_api_init', function () {
    VTSEO_Router::register_routes();
} );

/* ── Activation ───────────────────────────────────────────── */
// add_option() is a no-op if the option already exists, so this can safely
// re-run on every plugin update/reactivation without ever regenerating an
// existing site's token or resetting its enabled groups -- see README.md,
// "Does updating the plugin break my connection?".
register_activation_hook( __FILE__, function () {
    $token = bin2hex( random_bytes( 32 ) );
    add_option( 'vtseo_api_token', $token );
    add_option( 'vtseo_enabled',   '1' );
    add_option( 'vtseo_log_level', 'info' );
    add_option( 'vtseo_allowed_actions', json_encode( [
        'content', 'seo', 'media', 'site',
    ] ) );
    flush_rewrite_rules();
} );

register_deactivation_hook( __FILE__, function () {
    flush_rewrite_rules();
} );
