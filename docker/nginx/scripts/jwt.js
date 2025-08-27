// njs-compatible HS256 JWT verifier (no Node Buffer, no destructuring)
var crypto = require('crypto');

function b64url_to_b64(s) {
    // convert URL-safe base64 to standard base64 with padding
    s = s.replace(/-/g, '+').replace(/_/g, '/');
    var pad = s.length % 4;
    if (pad === 2) s += '==';
    else if (pad === 3) s += '=';
    else if (pad === 1) s += '===';
    return s;
}

function b64url_decode_to_string(s) {
    // atob is available in njs; header/payload are ASCII JSON
    return atob(b64url_to_b64(s));
}

function verify(r) {
    var prefix = 'Bearer ';
    // Some subrequest contexts may not expose headersIn; fall back to $http_authorization
    var auth = (r.headersIn && r.headersIn['Authorization'])
            || (r.variables && r.variables.auth_header)
            || r.variables.http_authorization;

    if (!auth || auth.substr(0, prefix.length) !== prefix) {
        r.error('Missing or invalid Authorization header');
        r.return(401);
        return;
    }

    var token = auth.substr(prefix.length);
    var parts = token.split('.');
    if (parts.length !== 3) {
        r.error('JWT is not a three-part token');
        r.return(401);
        return;
    }

    /* ---------- 1) Parse header & payload ---------- */
    var headerStr  = b64url_decode_to_string(parts[0]);
    var payloadStr = b64url_decode_to_string(parts[1]);

    var header, payload;
    try {
        header   = JSON.parse(headerStr);
        payload  = JSON.parse(payloadStr);
    } catch (e) {
        r.error('JWT header/payload JSON parse failed');
        r.return(401);
        return;
    }

    if (!header || header.alg !== 'HS256') {
        r.error('Unsupported JWT algorithm: ' + (header && header.alg));
        r.return(401, 'Unsupported JWT algorithm');
        return;
    }

    /* ---------- 2) Compute & compare signature ---------- */
    // Resolve secret from nginx variable set via js_set, with env fallback
    // Prefer baked-in conf variable (from template), then js_set variable, then env
    var fromBaked = (r.variables && r.variables.jwt_secret_baked) || '';
    var fromVar   = (r.variables && r.variables.jwt_shared_secret) || '';
    var fromEnv   = (typeof ngx !== 'undefined' && ngx.env && ngx.env.JWT_SHARED_SECRET) || '';
    var secret    = fromBaked || fromVar || fromEnv || '';
    if (!secret) {
        r.warn('JWT: secret missing (baked=' + (fromBaked ? 'y' : 'n') + ', var=' + (fromVar ? 'y' : 'n') + ', env=' + (fromEnv ? 'y' : 'n') + ')');
    }

    if (!secret) {
        // Treat missing secret as authentication failure, not server error,
        // to avoid 500s when env access isn't available in njs.
        r.error('JWT secret unavailable; treating as unauthorized');
        r.return(401, 'Unauthorized');
        return;
    }

    var signingInput = parts[0] + '.' + parts[1];
    var h            = crypto.createHmac('sha256', secret);
    var expectedB64  = h.update(signingInput).digest('base64');
    var expectedB64url = expectedB64
        .replace(/\+/g, '-')
        .replace(/\//g, '_')
        .replace(/=+$/, '');

    if (parts[2] !== expectedB64url) {
        r.error('JWT signature verification failed');
        r.return(401, 'Invalid JWT signature');
        return;
    }

    /* ---------- 3) Validate exp (optional) ---------- */
    var now = Math.floor(Date.now() / 1000);
    if (payload && payload.exp && payload.exp < now) {
        r.error('JWT has expired');
        r.return(401, 'Expired JWT');
        return;
    }

    /* ---------- Authorized ---------- */
    r.return(204);   // No-Content == success for auth_request
}

function get_secret(r) {
    // Called by js_set at init and per-request. Guard ngx for older njs.
    try {
        if (typeof ngx !== 'undefined' && ngx.env) {
            var s = ngx.env.JWT_SHARED_SECRET || '';
            if (!s) {
                r.warn('JWT: ngx.env present but JWT_SHARED_SECRET empty');
            }
            return s;
        }
    } catch (e) {
        // fall through
    }
    return '';
}

export default { verify, get_secret };
