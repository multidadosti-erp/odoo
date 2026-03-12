odoo.define('web.Loading', function (require) {
"use strict";

/**
 * Loading Indicator
 *
 * When the user performs an action, it is good to give him some feedback that
 * something is currently happening.  The purpose of the Loading Indicator is to
 * display a small rectangle on the bottom right of the screen with just the
 * text 'Loading' and the number of currently running rpcs.
 *
 * After a delay of 3s, if a rpc is still not completed, we also block the UI.
 */

var core = require('web.core');
var framework = require('web.framework');
var session = require('web.session');
var Widget = require('web.Widget');

var _t = core._t;
var MIN_LOADING_BLOCKED_AGE_MS = 3200;

function getPendingRpcDiagnostics() {
    if (typeof window === 'undefined' || !window.__odooRpcTraceState) {
        return [];
    }
    var pending = window.__odooRpcTraceState.pending || {};
    var now = Date.now();
    return _.chain(pending)
        .values()
        .filter(function (rpc) {
            if (!rpc || rpc.shadow) {
                return false;
            }
            if (rpc.url && rpc.url.indexOf('/longpolling/poll') !== -1) {
                return false;
            }
            return (now - rpc.startedAt) >= MIN_LOADING_BLOCKED_AGE_MS;
        })
        .map(function (rpc) {
            return {
                id: rpc.id,
                ageMs: now - rpc.startedAt,
                url: rpc.url,
                model: rpc.model,
                method: rpc.method,
                route: rpc.route,
                shadow: rpc.shadow,
            };
        })
        .sortBy(function (rpc) {
            return -rpc.ageMs;
        })
        .value();
}

function sendLoadingBlockedDiagnostics(pendingRpc) {
    if (typeof window === 'undefined' || typeof window.__odooSendRpcTraceEvent !== 'function') {
        return;
    }
    _.each((pendingRpc || []).slice(0, 10), function (rpc) {
        window.__odooSendRpcTraceEvent({
            category: 'loading_blocked',
            id: rpc.id,
            ageMs: rpc.ageMs,
            url: rpc.url,
            model: rpc.model,
            method: rpc.method,
            route: rpc.route,
        });
    });
}

var Loading = Widget.extend({
    template: "Loading",

    init: function(parent) {
        this._super(parent);
        this.count = 0;
        this.blocked_ui = false;
        core.bus.on('rpc_request', this, this.request_call);
        core.bus.on("rpc_response", this, this.response_call);
        core.bus.on("rpc_response_failed", this, this.response_call);
    },
    destroy: function() {
        this.on_rpc_event(-this.count);
        this._super();
    },
    request_call: function() {
        this.on_rpc_event(1);
    },
    response_call: function() {
        this.on_rpc_event(-1);
    },
    on_rpc_event : function(increment) {
        var self = this;
        if (!this.count && increment === 1) {
            // Block UI after 3s
            this.long_running_timer = setTimeout(function () {
                self.blocked_ui = true;
                var pendingRpc = getPendingRpcDiagnostics();
                if (pendingRpc.length) {
                    console.warn('[LOADING-BLOCKED] UI blocked by long running RPC(s)', pendingRpc);
                    sendLoadingBlockedDiagnostics(pendingRpc);
                }
                framework.blockUI();
            }, 3000);
        }

        this.count += increment;
        if (this.count > 0) {
            if (session.debug) {
                this.$el.text(_.str.sprintf( _t("Loading (%d)"), this.count));
            } else {
                this.$el.text(_t("Loading"));
            }
            this.$el.show();
            this.getParent().$el.addClass('oe_wait');
        } else {
            this.count = 0;
            clearTimeout(this.long_running_timer);
            // Don't unblock if blocked by somebody else
            if (self.blocked_ui) {
                this.blocked_ui = false;
                framework.unblockUI();
            }
            this.$el.fadeOut();
            this.getParent().$el.removeClass('oe_wait');
        }
    }
});

return Loading;
});

