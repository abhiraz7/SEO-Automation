<?php
/**
 * Registers every VtechSEO Agent REST endpoint under /wp-json/vtseo/v1/
 */
class VTSEO_Router {

    const NS = 'vtseo/v1';

    public static function init() {}

    public static function register_routes() {
        $perm = [ 'VTSEO_Auth', 'permission_callback' ];

        register_rest_route( self::NS, '/ping', [
            'methods'             => 'GET',
            'callback'            => [ __CLASS__, 'ping' ],
            'permission_callback' => $perm,
        ] );

        register_rest_route( self::NS, '/capabilities', [
            'methods'             => 'GET',
            'callback'            => [ __CLASS__, 'capabilities' ],
            'permission_callback' => $perm,
        ] );

        register_rest_route( self::NS, '/tool', [
            'methods'             => 'POST',
            'callback'            => [ __CLASS__, 'dispatch_tool' ],
            'permission_callback' => $perm,
        ] );

        register_rest_route( self::NS, '/logs', [
            'methods'             => 'GET',
            'callback'            => [ __CLASS__, 'get_logs' ],
            'permission_callback' => $perm,
        ] );

        /* ── MCP Streamable HTTP (JSON-RPC 2.0) ───────────── */
        register_rest_route( self::NS, '/mcp', [
            [
                'methods'             => 'POST',
                'callback'            => [ 'VTSEO_MCP', 'handle' ],
                'permission_callback' => $perm,
            ],
            [
                'methods'             => 'GET',
                'callback'            => [ 'VTSEO_MCP', 'handle_get' ],
                'permission_callback' => $perm,
            ],
        ] );
    }

    public static function ping(): WP_REST_Response {
        return new WP_REST_Response( [
            'status'  => 'ok',
            'plugin'  => 'VtechSEO Agent',
            'version' => VTSEO_VERSION,
            'site'    => get_bloginfo( 'url' ),
            'time'    => current_time( 'c' ),
        ] );
    }

    public static function capabilities(): WP_REST_Response {
        return new WP_REST_Response( [
            'tools' => self::tool_manifest(),
        ] );
    }

    public static function get_logs(): WP_REST_Response {
        return new WP_REST_Response( VTSEO_Logger::get_recent() );
    }

    /** Central dispatcher — receives { tool, params } */
    public static function dispatch_tool( WP_REST_Request $request ): WP_REST_Response {
        $body    = $request->get_json_params();
        $tool    = sanitize_key( $body['tool'] ?? '' );
        $params  = $body['params'] ?? [];
        $allowed = json_decode( get_option( 'vtseo_allowed_actions', '[]' ), true );

        VTSEO_Logger::log( 'info', "Tool called: {$tool}" );

        try {
            $result = self::call_tool( $tool, $params, $allowed );
            return new WP_REST_Response( [ 'success' => true, 'result' => $result ] );
        } catch ( InvalidArgumentException $e ) {
            VTSEO_Logger::log( 'error', "Tool {$tool} error: " . $e->getMessage() );
            return new WP_REST_Response( [ 'success' => false, 'error' => $e->getMessage() ], 400 );
        } catch ( Exception $e ) {
            VTSEO_Logger::log( 'error', "Tool {$tool} error: " . $e->getMessage() );
            // Return a generic message to avoid leaking internal paths/table names.
            return new WP_REST_Response( [ 'success' => false, 'error' => 'Tool execution failed. Check activity log for details.' ], 500 );
        }
    }

    /**
     * Shared tool executor — used by both the legacy /tool endpoint and the MCP handler.
     * Throws on unknown tool, disabled group, or handler error.
     */
    public static function call_tool( string $tool, array $params, array $allowed ) {
        $handlers = self::get_handlers();

        if ( ! isset( $handlers[ $tool ] ) ) {
            throw new InvalidArgumentException( "Unknown tool: {$tool}" );
        }

        [ $class, $method, $group ] = $handlers[ $tool ];

        if ( ! in_array( $group, $allowed, true ) ) {
            VTSEO_Logger::log( 'warn', "Blocked tool '{$tool}' — group '{$group}' not in allowed_actions." );
            throw new RuntimeException( "Tool group '{$group}' is disabled. Enable it in VtechSEO Agent settings." );
        }

        return call_user_func( [ $class, $method ], $params );
    }

    private static function get_handlers(): array {
        return [
            /* Content */
            'create_post'            => [ 'VTSEO_Content', 'create_post',        'content' ],
            'update_post'            => [ 'VTSEO_Content', 'update_post',        'content' ],
            'get_post'               => [ 'VTSEO_Content', 'get_post',           'content' ],
            'list_posts'             => [ 'VTSEO_Content', 'list_posts',         'content' ],
            'delete_post'            => [ 'VTSEO_Content', 'delete_post',        'content' ],
            'schedule_post'          => [ 'VTSEO_Content', 'schedule_post',      'content' ],
            'set_featured_image'     => [ 'VTSEO_Content', 'set_featured_image', 'content' ],
            'get_taxonomies'         => [ 'VTSEO_Content', 'get_taxonomies',     'content' ],
            'assign_terms'           => [ 'VTSEO_Content', 'assign_terms',       'content' ],
            /* SEO */
            'yoast_get_meta'         => [ 'VTSEO_SEO', 'get_meta',        'seo' ],
            'yoast_set_meta'         => [ 'VTSEO_SEO', 'set_meta',        'seo' ],
            'yoast_audit'            => [ 'VTSEO_SEO', 'audit_post',      'seo' ],
            'yoast_sitemap_ping'     => [ 'VTSEO_SEO', 'ping_sitemap',    'seo' ],
            /* Media */
            'upload_media'            => [ 'VTSEO_Media', 'upload',                'media' ],
            'list_media'              => [ 'VTSEO_Media', 'list_media',            'media' ],
            'get_media'               => [ 'VTSEO_Media', 'get_media',             'media' ],
            'delete_media'            => [ 'VTSEO_Media', 'delete_media',          'media' ],
            'update_media_meta'       => [ 'VTSEO_Media', 'update_meta',           'media' ],
            'update_media_alt_by_url' => [ 'VTSEO_Media', 'update_alt_by_url',     'media' ],
            /* Site (read-only diagnostics + narrow, allow-listed writes) */
            'get_site_info'          => [ 'VTSEO_Site', 'get_site_info', 'site' ],
            'list_plugins'           => [ 'VTSEO_Site', 'list_plugins',  'site' ],
            'get_options'            => [ 'VTSEO_Site', 'get_options',   'site' ],
            'flush_cache'            => [ 'VTSEO_Site', 'flush_cache',   'site' ],
        ];
    }

    private static function tool_manifest(): array {
        return [
            /* ── Content ── */
            [ 'name' => 'create_post',            'group' => 'content', 'description' => 'Create any post type (post, page, custom). Supports scheduling, excerpt, password, custom fields.' ],
            [ 'name' => 'update_post',            'group' => 'content', 'description' => 'Update any field of an existing post.' ],
            [ 'name' => 'get_post',               'group' => 'content', 'description' => 'Get full post data including meta and terms.' ],
            [ 'name' => 'list_posts',             'group' => 'content', 'description' => 'List posts with filters (type, status, author, date, search).' ],
            [ 'name' => 'delete_post',            'group' => 'content', 'description' => 'Trash or permanently delete a post.' ],
            [ 'name' => 'schedule_post',          'group' => 'content', 'description' => 'Schedule a post to publish at a specific datetime.' ],
            [ 'name' => 'set_featured_image',     'group' => 'content', 'description' => 'Set or remove the featured image on a post.' ],
            [ 'name' => 'get_taxonomies',         'group' => 'content', 'description' => 'List all taxonomies and their terms.' ],
            [ 'name' => 'assign_terms',           'group' => 'content', 'description' => 'Add/set/remove taxonomy terms on a post.' ],

            /* ── SEO ── */
            [ 'name' => 'yoast_get_meta',         'group' => 'seo', 'description' => 'Get all Yoast SEO meta for a post.' ],
            [ 'name' => 'yoast_set_meta',         'group' => 'seo', 'description' => 'Set Yoast SEO meta (title, description, robots, og, canonical, schema).' ],
            [ 'name' => 'yoast_audit',            'group' => 'seo', 'description' => 'Run a readability/keyword audit and return recommendations.' ],
            [ 'name' => 'yoast_sitemap_ping',     'group' => 'seo', 'description' => 'Ping search engines with updated sitemap.' ],

            /* ── Media ── */
            [ 'name' => 'upload_media',            'group' => 'media', 'description' => 'Upload an image/file from URL or base64 to the media library.' ],
            [ 'name' => 'list_media',              'group' => 'media', 'description' => 'Search and list media library items.' ],
            [ 'name' => 'get_media',               'group' => 'media', 'description' => 'Get a single media item with all sizes and meta.' ],
            [ 'name' => 'delete_media',            'group' => 'media', 'description' => 'Delete a media item.' ],
            [ 'name' => 'update_media_meta',       'group' => 'media', 'description' => 'Update alt text, caption, title, description by media_id.' ],
            [ 'name' => 'update_media_alt_by_url', 'group' => 'media', 'description' => 'Update alt text for an attachment by its public URL -- resolves the URL to a media_id first via WordPress core, for images (e.g. logo, theme header images) where the caller only has the rendered <img src>, not the internal attachment ID.' ],

            /* ── Site ── */
            [ 'name' => 'get_site_info',          'group' => 'site', 'description' => 'Get WordPress site info: name, URL, version, active theme, post/user/media counts.' ],
            [ 'name' => 'list_plugins',           'group' => 'site', 'description' => 'List installed plugins (name, version, active status) -- read-only, no install/activate/deactivate.' ],
            [ 'name' => 'get_options',             'group' => 'site', 'description' => 'Read specific wp_options by key -- sensitive keys (auth salts, active_plugins, the API token itself, etc.) are always blocked.' ],
            [ 'name' => 'flush_cache',             'group' => 'site', 'description' => 'Flush the WordPress object cache, e.g. after a fix so it is visible immediately instead of stuck behind a stale cache.' ],
        ];
    }
}
