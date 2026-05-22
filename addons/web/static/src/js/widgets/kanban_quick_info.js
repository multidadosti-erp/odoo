odoo.define('web.KanbanQuickInfoWidget', function (require) {
"use strict";

var core = require('web.core');
var Dialog = require('web.Dialog');
var Widget = require('web.Widget');
var widgetRegistry = require('web.widget_registry');

var _t = core._t;

var KanbanQuickInfoWidget = Widget.extend({
    className: 'o_kanban_quick_info_widget',
    events: {
        'click .o_kanban_quick_info_button': '_onClickQuickInfo',
    },

    /**
     * @override
     * @param {Widget} parent
     * @param {Object} state
     * @param {Object} options
     */
    init: function (parent, state, options) {
        this._super.apply(this, arguments);
        this.state = state || {};
        this.attrs = (options && options.attrs) || {};
        this.nodeOptions = (options && options.nodeOptions) || {};
        this.config = this._normalizeConfig();
    },

    /**
     * @override
     */
    start: function () {
        this._renderButton();
        return this._super.apply(this, arguments);
    },

    //--------------------------------------------------------------------------
    // Private
    //--------------------------------------------------------------------------

    /**
     * @private
     * @returns {Object}
     */
    _normalizeConfig: function () {
        var options = _.extend({}, this._parseJSONLike(this.attrs.options), this.nodeOptions);
        var fieldsValue = this.attrs.fields || options.fields || this.attrs.fields_csv || options.fields_csv || '';
        var fields = _.isArray(fieldsValue) ? fieldsValue : String(fieldsValue).split(',');
        var htmlFieldsValue = this.attrs.html_fields || options.html_fields || options.htmlFields || '';
        var htmlFields = _.isArray(htmlFieldsValue) ? htmlFieldsValue : String(htmlFieldsValue).split(',');

        return {
            title: this.attrs.title || options.title || _t('Quick information'),
            icon: this.attrs.icon || options.icon || 'fa-info-circle',
            fields: _.chain(fields)
                .map(function (fieldName) {
                    return String(fieldName || '').trim();
                })
                .filter(function (fieldName) {
                    return !!fieldName;
                })
                .uniq()
                .value(),
            htmlFields: _.chain(htmlFields)
                .map(function (fieldName) {
                    return String(fieldName || '').trim();
                })
                .filter(function (fieldName) {
                    return !!fieldName;
                })
                .uniq()
                .value(),
        };
    },

    /**
     * @private
     * @param {string} value
     * @returns {Object}
     */
    _parseJSONLike: function (value) {
        if (!value || !_.isString(value)) {
            return {};
        }
        try {
            var normalized = value
                .replace(/\bTrue\b/g, 'true')
                .replace(/\bFalse\b/g, 'false')
                .replace(/'/g, '"');
            return JSON.parse(normalized);
        } catch (e) {
            return {};
        }
    },

    /**
     * @private
     */
    _renderButton: function () {
        this.$el.empty();
        if (!this.config.fields.length) {
            return;
        }

        $('<button/>', {
            type: 'button',
            class: 'btn btn-link p-0 o_kanban_quick_info_button o_kanban_tooltip_dialog_button',
            title: this.config.title,
            'aria-label': this.config.title,
        }).append(
            $('<i/>', { class: 'fa ' + this.config.icon })
        ).appendTo(this.$el);
    },

    /**
     * @private
     * @param {string} fieldName
     * @returns {string}
     */
    _getFieldLabel: function (fieldName) {
        var parent = this.getParent();
        var fieldInfo = (parent.fieldsInfo && parent.fieldsInfo[fieldName]) || {};
        var fieldDef = (parent.fields && parent.fields[fieldName]) || {};
        return fieldInfo.string || fieldDef.string || fieldName;
    },

    /**
     * @private
     * @param {string} fieldName
     * @returns {string}
     */
    _getFormattedValue: function (fieldName) {
        var parent = this.getParent();
        var fieldDef = (parent.fields && parent.fields[fieldName]) || {};
        var fieldValue = parent.record && parent.record[fieldName];
        var raw = fieldValue ? fieldValue.raw_value : undefined;
        var value = fieldValue ? fieldValue.value : undefined;

        if ((fieldDef.type === 'many2one' || fieldDef.type === 'reference') && _.isArray(raw)) {
            return raw[1] || '-';
        }
        if (fieldDef.type === 'many2many' && _.isArray(raw)) {
            return raw.length ? raw.join(', ') : '';
        }
        if (fieldDef.type === 'boolean') {
            return raw ? _t('Yes') : _t('No');
        }
        if (value === false || _.isUndefined(value) || _.isNull(value) || value === '') {
            return '';
        }
        return String(value);
    },

    /**
     * @private
     * @param {string} fieldName
     * @returns {boolean}
     */
    _isHtmlField: function (fieldName) {
        if (_.contains(this.config.htmlFields || [], fieldName)) {
            return true;
        }
        var parent = this.getParent();
        var fieldDef = (parent.fields && parent.fields[fieldName]) || {};
        return fieldDef.type === 'html';
    },

    /**
     * @private
     * @param {string} html
     * @returns {boolean}
     */
    _isEmptyHtml: function (html) {
        var $wrapper = $('<div/>').html(html || '');
        $wrapper.find('script,style').remove();

        var text = $wrapper.text().replace(/\u00a0/g, ' ').trim();
        var hasMedia = $wrapper.find('img,video,iframe,object,embed,svg,table,ul,ol').length > 0;
        return !text && !hasMedia;
    },

    /**
     * @private
     * @param {JQuery} $wrapper
     */
    _normalizeHtmlForDialog: function ($wrapper) {
        // Remove empty blocks generated by html editors (<p><br></p>, etc.)
        $wrapper.find('p,div,li').each(function () {
            var $el = $(this);
            var hasMedia = $el.find('img,video,iframe,object,embed,svg,table').length > 0;
            var text = $el.text().replace(/\u00a0/g, ' ').trim();
            if (!hasMedia && !text && $el.find('br').length <= 1) {
                $el.remove();
            }
        });

        // Keep compact line spacing, similar to form preview.
        $wrapper.find('p,div,ul,ol,li').css({
            margin: '0',
            padding: '0',
            lineHeight: '1.35',
        });
        $wrapper.find('ul,ol').css({
            paddingLeft: '16px',
        });
    },

    /**
     * @private
     * @param {string} fieldName
     * @param {string} formattedValue
     * @returns {string}
     */
    _getValueHTML: function (fieldName, formattedValue) {
        if (!this._isHtmlField(fieldName)) {
            return _.escape(formattedValue);
        }

        if (!formattedValue || this._isEmptyHtml(formattedValue)) {
            return '';
        }

        var $wrapper = $('<div/>').html(formattedValue);
        $wrapper.find('script,style').remove();
        this._normalizeHtmlForDialog($wrapper);
        return $wrapper.html();
    },

    //--------------------------------------------------------------------------
    // Handlers
    //--------------------------------------------------------------------------

    /**
     * @private
     * @param {MouseEvent} ev
     */
    _onClickQuickInfo: function (ev) {
        ev.preventDefault();
        ev.stopPropagation();

        if (!this.config.fields.length) {
            return;
        }

        var self = this;
        var rows = _.map(this.config.fields, function (fieldName, index) {
            var rowBg = (index % 2) ? '#fbfbfc' : '#ffffff';
            var formattedValue = self._getFormattedValue(fieldName);
            var valueHTML = self._getValueHTML(fieldName, formattedValue);
            return '<tr style="background-color: ' + rowBg + ';">' +
                '<td style="padding: 2px 4px; vertical-align: top;">' +
                    '<span style="font-weight: 600;">' + _.escape(self._getFieldLabel(fieldName)) + '</span>' +
                    '<span style="color: #8b8f97; margin: 0 3px 0 2px;">:</span>' +
                    '<span>' + valueHTML + '</span>' +
                '</td>' +
                '</tr>';
        }).join('');

        var $content = $('<div/>').append(
            $('<table class="table table-sm table-borderless mb-0"/>').append('<tbody>' + rows + '</tbody>')
        );

        new Dialog(this, {
            title: this.config.title,
            $content: $content,
            buttons: [{
                text: _t('Close'),
                close: true,
            }],
        }).open();
    },
});

widgetRegistry.add('kanban_quick_info', KanbanQuickInfoWidget);

return KanbanQuickInfoWidget;
});
