odoo.define('web.KanbanRecord', function (require) {
"use strict";

/**
 * This file defines the KanbanRecord widget, which corresponds to a card in
 * a Kanban view.
 */
var config = require('web.config');
var core = require('web.core');
var Domain = require('web.Domain');
var field_utils = require('web.field_utils');
var utils = require('web.utils');
var Widget = require('web.Widget');
var ajax = require('web.ajax');
var widgetRegistry = require('web.widget_registry');

var _t = core._t;
var QWeb = core.qweb;

var KANBAN_RECORD_COLORS = [
    _t('No color'),
    _t('Red'),
    _t('Orange'),
    _t('Yellow'),
    _t('Light blue'),
    _t('Dark purple'),
    _t('Salmon pink'),
    _t('Medium blue'),
    _t('Dark blue'),
    _t('Fushia'),
    _t('Green'),
    _t('Purple'),
];
var NB_KANBAN_RECORD_COLORS = KANBAN_RECORD_COLORS.length;
var highchartsLoadDef;

function _ensureHighchartsLoaded() {
    if (window.Highcharts) {
        return $.when();
    }
    if (highchartsLoadDef) {
        return highchartsLoadDef;
    }

    highchartsLoadDef = $.getScript('/web/static/lib/highcharts/highcharts.js').then(function () {
        if (window.Highcharts && window.Highcharts.seriesTypes && window.Highcharts.seriesTypes.funnel) {
            return;
        }
        return $.getScript('/web/static/lib/highcharts/modules/funnel.js');
    }).fail(function () {
        highchartsLoadDef = null;
    });

    return highchartsLoadDef;
}

var KanbanRecord = Widget.extend({
    events: {
        'click .oe_kanban_action': '_onKanbanActionClicked',
        'click .o_kanban_manage_toggle_button': '_onManageTogglerClicked',
    },
    /**
     * @override
     */
    init: function (parent, state, options) {
        this._super(parent);

        var stateObj = _.isObject(state) ? state : {};

        var parentState = (parent && parent.data) || {};
        var parentFieldsInfo = parentState.fieldsInfo && parentState.fieldsInfo.kanban;

        this.fields = stateObj.fields || parentState.fields || {};
        this.fieldsInfo = (stateObj.fieldsInfo && stateObj.fieldsInfo.kanban) || parentFieldsInfo || {};
        this.modelName = stateObj.model || parentState.model;

        this.options = options;
        this.editable = options.editable;
        this.deletable = options.deletable;
        this._kanbanCharts = [];
        this._kanbanChartRetryTimeout = null;
        this.read_only_mode = options.read_only_mode;
        this.qweb = options.qweb;
        this.subWidgets = {};
        
        if (options.groupReadonlyAttr == undefined) {
            this.group_readonly = false;    
        }
        else {
            var stateData = stateObj.data || {};
            this.group_readonly = options.groupReadonlyAttr.length ? new Domain(options.groupReadonlyAttr, stateData).compute(stateData) : false;
        }

        this._setState(stateObj);
        // avoid quick multiple clicks
        this._onKanbanActionClicked = _.debounce(this._onKanbanActionClicked, 300, true);
    },
    /**
     * @override
     */
    start: function () {
        return $.when(this._super.apply(this, arguments), this._render());
    },
    /**
     * Called each time the record is attached to the DOM.
     */
    on_attach_callback: function () {
        _.invoke(this.subWidgets, 'on_attach_callback');
        if (this.$ && this.$('[data-kanban-chart]').length) {
            this._renderKanbanCharts();
        }
    },
    /**
     * Called each time the record is detached from the DOM.
     */
    on_detach_callback: function () {
        _.invoke(this.subWidgets, 'on_detach_callback');
        if (this._kanbanChartRetryTimeout) {
            clearTimeout(this._kanbanChartRetryTimeout);
            this._kanbanChartRetryTimeout = null;
        }
    },

    //--------------------------------------------------------------------------
    // Public
    //--------------------------------------------------------------------------

    /**
     * Re-renders the record with a new state
     *
     * @param {Object} state
     * @returns {Deferred}
     */
    update: function (state) {
        // detach the widgets because the record will empty its $el, which will
        // remove all event handlers on its descendants, and we want to keep
        // those handlers alive as we will re-use these widgets
        _.invoke(_.pluck(this.subWidgets, '$el'), 'detach');
        this._setState(state);
        return this._render();
    },

    //--------------------------------------------------------------------------
    // Private
    //--------------------------------------------------------------------------

    /**
     * @private
     */
    _attachTooltip: function () {
        var self = this;
        this.$('[tooltip]').each(function () {
            var $el = $(this);
            var tooltip = $el.attr('tooltip');
            if (tooltip) {
                $el.tooltip({
                    title: self.qweb.render(tooltip, self.qweb_context)
                });
            }
        });
    },
    /**
     * @private
     * @param {string} d a stringified domain
     * @returns {boolean} the domain evaluted with the current values
     */
    _computeDomain: function (d) {
        var evalContext = _.extend({}, this.recordData || {}, (this.state && this.state.evalContext) || {});
        if (!evalContext.context) {
            evalContext.context = (this.state && this.state.context) || {};
        }
        if (!evalContext.parent) {
            evalContext.parent = {};
        }
        try {
            return new Domain(d).compute(evalContext);
        } catch (e) {
            return false;
        }
    },
    /**
     * Generates the color classname from a given variable
     *
     * @private
     * @param {number | string} variable
     * @return {string} the classname
     */
    _getColorClassname: function (variable) {
        var color = this._getColorID(variable);
        return 'oe_kanban_color_' + color;
    },
    /**
     * Computes a color id between 0 and 10 from a given value
     *
     * @private
     * @param {number | string} variable
     * @returns {integer} the color id
     */
    _getColorID: function (variable) {
        if (typeof variable === 'number') {
            return Math.round(variable) % NB_KANBAN_RECORD_COLORS;
        }
        if (typeof variable === 'string') {
            var index = 0;
            for (var i = 0 ; i < variable.length ; i++) {
                index += variable.charCodeAt(i);
            }
            return index % NB_KANBAN_RECORD_COLORS;
        }
        return 0;
    },
    /**
     * Computes a color name from value
     *
     * @private
     * @param {number | string} variable
     * @returns {integer} the color name
     */
    _getColorname: function (variable) {
        var colorID = this._getColorID(variable);
        return KANBAN_RECORD_COLORS[colorID];
    },
    file_type_magic_word: {
        '/': 'jpg',
        'R': 'gif',
        'i': 'png',
        'P': 'svg+xml',
    },
    /**
     * @private
     * @param {string} model the name of the model
     * @param {string} field the name of the field
     * @param {integer} id the id of the resource
     * @param {integer} cache the cache duration, in seconds
     * @param {Object} options
     * @returns {string} the url of the image
     */
    _getImageURL: function (model, field, id, cache, options) {
        options = options || {};
        var url;
        var hasRawField = this.recordData && Object.prototype.hasOwnProperty.call(this.recordData, field);
        var rawFieldValue = hasRawField ? this.recordData[field] : undefined;

        if (hasRawField && rawFieldValue && !utils.is_bin_size(rawFieldValue)) {
            // Use magic-word technique for detecting image type
            url = 'data:image/' + this.file_type_magic_word[rawFieldValue[0]] + ';base64,' + rawFieldValue;
        } else if (hasRawField && !rawFieldValue) {
            url = "/web/static/src/img/placeholder.png";
        } else {
            if (_.isArray(id)) { id = id[0]; }
            if (!id) {
                id = this.id || this.db_id || undefined;
            }
            if (options.preview_image)
                field = options.preview_image;
            var lastUpdateValue = this.record.__last_update && this.record.__last_update.value;
            var unique = lastUpdateValue ? String(lastUpdateValue).replace(/[^0-9]/g, '') : undefined;
            var session = this.getSession();
            url = session.url('/web/image', {model: model, field: field, id: id, unique: unique});
            if (cache !== undefined) {
                // Set the cache duration in seconds.
                url += '&cache=' + parseInt(cache, 10);
            }
        }
        return url;
    },
    /**
     * Triggers up an event to open the record
     *
     * @private
     */
    _openRecord: function () {
        if (this.$el.hasClass('o_currently_dragged')) {
            // this record is currently being dragged and dropped, so we do not
            // want to open it.
            return;
        }
        var editMode = this.$el.hasClass('oe_kanban_global_click_edit');
        this.trigger_up('open_record', {
            id: this.db_id,
            mode: editMode ? 'edit' : 'readonly',
        });
    },
    /**
     * Processes each 'field' tag and replaces it by the specified widget, if
     * any, or directly by the formatted value
     *
     * @private
     */
    _processFields: function () {
        var self = this;
        this.$("field").each(function () {
            var $field = $(this);
            var field_name = $field.attr("name");
            var field_widget = $field.attr("widget");
            var fieldDef = self.fields[field_name];
            var fieldInfo = self.fieldsInfo[field_name] || {};

            if (!fieldDef) {
                return;
            }

            // a widget is specified for that field or a field is a many2many ;
            // in this latest case, we want to display the widget many2manytags
            // even if it is not specified in the view.
            if (field_widget || fieldDef.type === 'many2many') {
                var widget = self.subWidgets[field_name];
                if (!widget) {
                    // the widget doesn't exist yet, so instanciate it
                    var Widget = fieldInfo.Widget;
                    if (Widget) {
                        widget = self._processWidget($field, field_name, Widget);
                        self.subWidgets[field_name] = widget;
                    } else if (config.debug) {
                        // the widget is not implemented
                        $field.replaceWith($('<span>', {
                            text: _.str.sprintf(_t('[No widget %s]'), field_widget),
                        }));
                    }
                } else {
                    // a widget already exists for that field, so reset it with the new state
                    widget.reset(self.state);
                    $field.replaceWith(widget.$el);
                }
            } else {
                self._processField($field, field_name);
            }
        });
    },
    /**
     * Replace a field by its formatted value.
     *
     * @private
     * @param {JQuery} $field
     * @param {String} field_name
     * @returns {Jquery} the modified node
     */
    _processField: function ($field, field_name) {
        // no widget specified for that field, so simply use a formatter
        // note: we could have used the widget corresponding to the field's type, but
        // it is much more efficient to use a formatter
        var field = this.fields[field_name];
        var value = this.recordData[field_name];
        var options = { data: this.recordData };
        var formatted_value = field_utils.format[field.type](value, field, options);
        var $result = $('<span>', {
            text: formatted_value,
        });
        $field.replaceWith($result);
        this._setFieldDisplay($result, field_name);
        return $result;
    },
    /**
     * Replace a field by its corresponding widget.
     *
     * @private
     * @param {JQuery} $field
     * @param {String} field_name
     * @param {Class} Widget
     * @returns {Widget} the widget instance
     */
    _processWidget: function ($field, field_name, Widget) {
        // some field's attrs might be record dependent (they start with
        // 't-att-') and should thus be evaluated, which is done by qweb
        // we here replace those attrs in the dict of attrs of the state
        // by their evaluted value, to make it transparent from the
        // field's widgets point of view
        // that dict being shared between records, we don't modify it
        // in place
        var self = this;
        var fieldInfo = this.fieldsInfo[field_name] || {};
        var attrs = Object.create(null);
        _.each(fieldInfo, function (value, key) {
            if (_.str.startsWith(key, 't-att-')) {
                key = key.slice(6);
                value = $field.attr(key);
            }
            attrs[key] = value;
        });
        var options = _.extend({}, this.options, {attrs: attrs});
        var widget = new Widget(this, field_name, this.state, options);
        var def = widget.replace($field).then(function () {
            self._setFieldDisplay(widget.$el, field_name);
        });
        if (def.state() === 'pending') {
            this.defs.push(def);
        }
        return widget;
    },
    _processWidgets: function () {
        var self = this;
        this.$("widget").each(function () {
            var $field = $(this);
            var Widget = widgetRegistry.get($field.attr('name'));
            var widget = new Widget(self, self.state);

            var def = widget._widgetRenderAndInsert(function () {}).then(function () {
                widget.$el.addClass('o_widget');
                $field.replaceWith(widget.$el);
            });
            if (def.state() === 'pending') {
                self.defs.push(def);
            }
        });
    },
    /**
     * @private
     * @param {string} raw
     * @param {*} fallback
     * @returns {*}
     */
    _parseKanbanChartJSONAttr: function (raw, fallback) {
        if (!raw) {
            return fallback;
        }
        try {
            return JSON.parse(raw);
        } catch (e) {
            return fallback;
        }
    },
    /**
     * @private
     * @param {JQuery} $chart
     * @param {string} message
     */
    _showKanbanChartError: function ($chart, message) {
        if (!$chart || !$chart.length) {
            return;
        }
        $chart.empty();
        $('<div/>', {
            class: 'o_kanban_chart_error',
            text: message,
        }).css({
            color: '#9f1239',
            padding: '12px',
            fontSize: '12px',
            textAlign: 'center',
        }).appendTo($chart);
    },
    /**
     * @private
     * @param {string} chartType
     * @param {*} dataset
     * @param {string} seriesName
     * @returns {Object}
     */
    _normalizeKanbanChartDataset: function (chartType, dataset, seriesName) {
        var normalized = {
            categories: [],
            series: [{
                name: seriesName,
                data: [],
            }],
        };

        if (_.isObject(dataset) && _.isArray(dataset.series)) {
            normalized.categories = _.isArray(dataset.categories) ? dataset.categories : [];
            normalized.series = dataset.series;
            return normalized;
        }

        if (chartType === 'funnel') {
            normalized.series[0].data = dataset || [];
            return normalized;
        }

        if (chartType === 'pie') {
            normalized.series[0].colorByPoint = true;
            normalized.series[0].data = _.map(dataset || [], function (item) {
                return {
                    name: item[0],
                    y: item[1],
                };
            });
            return normalized;
        }

        normalized.categories = _.map(dataset || [], function (item) { return item[0]; });
        normalized.series[0].data = _.map(dataset || [], function (item) { return item[1]; });
        return normalized;
    },
    /**
     * @private
     * @param {string} chartType
     * @param {string} title
     * @param {Object} normalized
     * @returns {Object}
     */
    _buildKanbanChartOptions: function (chartType, title, normalized) {
        if (chartType === 'funnel') {
            return {
                chart: {
                    type: 'funnel',
                    marginRight: 90,
                    backgroundColor: 'transparent',
                },
                title: {
                    text: title,
                    style: {
                        fontSize: '13px',
                    },
                },
                legend: {
                    enabled: false,
                },
                plotOptions: {
                    series: {
                        dataLabels: {
                            enabled: true,
                            inside: false,
                            format: '<b>{point.name}</b> ({point.y:,.0f})',
                            softConnector: true,
                        },
                        neckWidth: '42%',
                        neckHeight: '48%',
                    },
                },
                series: normalized.series,
                responsive: {
                    rules: [{
                        condition: { maxWidth: 560 },
                        chartOptions: {
                            plotOptions: {
                                series: {
                                    dataLabels: {
                                        inside: true,
                                    },
                                },
                            },
                        },
                    }],
                },
            };
        }

        if (chartType === 'pie') {
            return {
                chart: {
                    type: 'pie',
                    backgroundColor: 'transparent',
                },
                title: {
                    text: title,
                    style: {
                        fontSize: '13px',
                    },
                },
                legend: {
                    enabled: false,
                },
                plotOptions: {
                    pie: {
                        allowPointSelect: true,
                        cursor: 'pointer',
                        dataLabels: {
                            enabled: true,
                            format: '<b>{point.name}</b>: {point.y:,.0f}',
                        },
                    },
                },
                series: normalized.series,
            };
        }

        return {
            chart: {
                type: 'bar',
                backgroundColor: 'transparent',
            },
            title: {
                text: title,
                style: {
                    fontSize: '13px',
                },
            },
            xAxis: {
                categories: normalized.categories,
                title: {
                    text: null,
                },
            },
            yAxis: {
                min: 0,
                title: {
                    text: normalized.series[0] && normalized.series[0].name,
                    align: 'high',
                },
                labels: {
                    overflow: 'justify',
                },
            },
            legend: {
                enabled: false,
            },
            plotOptions: {
                bar: {
                    dataLabels: {
                        enabled: true,
                    },
                },
            },
            series: normalized.series,
        };
    },
    /**
     * Render embedded charts declared in kanban templates with data attributes.
     *
     * Usage in template:
     * <div
     *   data-kanban-chart="funnel"
     *   data-kanban-chart-model="crm.lead"
     *   data-kanban-chart-method="get_lead_stage_data"
     *   data-kanban-chart-title="Lead Funnel"
     * />
     *
     * @private
     */
    _renderKanbanCharts: function () {
        var self = this;

        this._destroyKanbanCharts();

        var $chartNodes = this.$('[data-kanban-chart]');
        if (!$chartNodes.length) {
            return;
        }

        $chartNodes.each(function () {
            var $chart = $(this);
            if (!$chart.children().length) {
                $('<div/>', {
                    class: 'o_kanban_chart_loading',
                    text: _t('Loading chart...'),
                }).css({
                    color: '#334155',
                    padding: '10px',
                    fontSize: '12px',
                    textAlign: 'center',
                }).appendTo($chart);
            }
        });

        _ensureHighchartsLoaded().then(function () {
            if (!window.Highcharts) {
                $chartNodes.each(function () {
                    self._showKanbanChartError($(this), _t('Chart library is not available.'));
                });
                return;
            }

            $chartNodes.each(function () {
                var $chart = $(this);
                var chartType = $chart.data('kanban-chart') || 'funnel';
                var model = $chart.data('kanban-chart-model') || self.modelName;
                var method = $chart.data('kanban-chart-method');
                var title = $chart.data('kanban-chart-title') || _t('Kanban Chart');
                var seriesName = $chart.data('kanban-chart-series-name') || _t('Value');
                var args = self._parseKanbanChartJSONAttr($chart.attr('data-kanban-chart-args'), []);
                var kwargs = self._parseKanbanChartJSONAttr($chart.attr('data-kanban-chart-kwargs'), {});
                var customOptions = self._parseKanbanChartJSONAttr($chart.attr('data-kanban-chart-options'), {});

                if (!method) {
                    self._showKanbanChartError($chart, _t('Chart method is not configured.'));
                    return;
                }

                if (!_.isArray(args)) {
                    args = [args];
                }
                if (!_.isObject(kwargs)) {
                    kwargs = {};
                }

                // Keep chart RPC behavior consistent with webclient calls by
                // propagating session + view/action context.
                var rpcContext = _.extend(
                    {},
                    (self.getSession() && self.getSession().user_context) || {},
                    (self.qweb_context && self.qweb_context.context) || {},
                    kwargs.context || {}
                );
                var rpcKwargs = _.extend({}, kwargs, {context: rpcContext});

                self._rpc({
                    model: model,
                    method: method,
                    args: args,
                    kwargs: rpcKwargs,
                }).then(function (dataset) {
                    if (!$chart.closest(document.documentElement).length) {
                        if (!self._kanbanChartRetryTimeout) {
                            self._kanbanChartRetryTimeout = setTimeout(function () {
                                self._kanbanChartRetryTimeout = null;
                                if (self.$el && self.$el.closest(document.documentElement).length) {
                                    self._renderKanbanCharts();
                                }
                            }, 80);
                        }
                        return;
                    }

                    var chart;
                    try {
                        var resolveSpecialChartValues = function (value) {
                            if (_.isArray(value)) {
                                return _.map(value, resolveSpecialChartValues);
                            }
                            if (_.isObject(value)) {
                                var out = {};
                                _.each(value, function (innerValue, key) {
                                    out[key] = resolveSpecialChartValues(innerValue);
                                });
                                return out;
                            }
                            if (value === 'now') {
                                return Date.now();
                            }
                            return value;
                        };

                        var normalized = self._normalizeKanbanChartDataset(chartType, dataset, seriesName);
                        var baseOptions = self._buildKanbanChartOptions(chartType, title, normalized);
                        var chartOptions = $.extend(true, {}, baseOptions, resolveSpecialChartValues(customOptions));
                        chart = window.Highcharts.chart($chart[0], chartOptions);
                    } catch (err) {
                        self._showKanbanChartError($chart, _t('Error while rendering chart.'));
                        return;
                    }

                    if (chart) {
                        self._kanbanCharts.push(chart);
                    }
                }).fail(function (err) {
                    self._showKanbanChartError($chart, _t('Error loading chart data.'));
                });
            });
        }).fail(function () {
            $chartNodes.each(function () {
                self._showKanbanChartError($(this), _t('Error loading chart library.'));
            });
        });
    },
    /**
     * Renders the record
     *
     * @returns {Deferred}
     */
    _render: function () {
        var self = this;
        this.defs = [];
        this._destroyKanbanCharts();
        this._replaceElement(this.qweb.render('kanban-box', this.qweb_context));
        this.$el.addClass('o_kanban_record').attr("tabindex",0);

        this.$el.attr('role', 'article');
        this.$el.data('record', this);
        if (this.$el.hasClass('oe_kanban_global_click') ||
            this.$el.hasClass('oe_kanban_global_click_edit')) {
            this.$el.on('click', this._onGlobalClick.bind(this));
            this.$el.on('keydown', this._onKeyDownCard.bind(this));
        } else {
            this.$el.on('keydown', this._onKeyDownOpenFirstLink.bind(this));
        }
        this._processFields();
        this._processWidgets();
        this._setupColor();
        this._setupColorPicker();
        this._attachTooltip();

        // We use boostrap tooltips for better and faster display
        this.$('span.o_tag').tooltip({delay: {'show': 50}});

        return $.when.apply(this, this.defs).then(function () {
            self._renderKanbanCharts();
        });
    },
    /**
     * @private
     */
    _destroyKanbanCharts: function () {
        _.each(this._kanbanCharts || [], function (chart) {
            if (!chart || !chart.destroy || chart.__kanbanDestroyed) {
                return;
            }

            // Highcharts may already have torn down part of the instance
            // during view switches; avoid throwing on double-destroy.
            if (!chart.renderer) {
                chart.__kanbanDestroyed = true;
                return;
            }

            try {
                chart.destroy();
            } catch (err) {
            }
            chart.__kanbanDestroyed = true;
        });
        this._kanbanCharts = [];
    },
    /**
     * Sets particular classnames on a field $el according to the
     * field's attrs (display or bold attributes)
     *
     * @private
     * @param {JQuery} $el
     * @param {string} fieldName
     */
    _setFieldDisplay: function ($el, fieldName) {
        var fieldInfo = this.fieldsInfo[fieldName] || {};
        // attribute display
        if (fieldInfo.display === 'right') {
            $el.addClass('float-right');
        } else if (fieldInfo.display === 'full') {
            $el.addClass('o_text_block');
        }

        // attribute bold
        if (fieldInfo.bold) {
            $el.addClass('o_text_bold');
        }
    },
    /**
     * @override
     */
    destroy: function () {
        if (this._kanbanChartRetryTimeout) {
            clearTimeout(this._kanbanChartRetryTimeout);
            this._kanbanChartRetryTimeout = null;
        }
        this._destroyKanbanCharts();
        this._super.apply(this, arguments);
    },
    /**
     * Sets internal values of the kanban record according to the given state
     *
     * @private
     * @param {Object} recordState
     */
    _setState: function (recordState) {
        var state = _.isObject(recordState) ? recordState : {};
        if (!_.isObject(state.data)) {
            state.data = {};
        }

        this.state = state;
        this.id = state.res_id;
        this.db_id = state.id;
        this.recordData = state.data || {};
        this.record = this._transformRecord(this.recordData);

        //  Multidados - Adiciona no widget, os campos 'now' e 'today',
        //  para facilitar a obtenção do dia de hoje e o horário.
        this.now = new moment();
        this.today = this.now.hour(0).minute(0).second(0).millisecond(0);
        //

        this.qweb_context = {
            kanban_image: this._getImageURL.bind(this),
            kanban_color: this._getColorClassname.bind(this),
            kanban_getcolor: this._getColorID.bind(this),
            kanban_getcolorname: this._getColorname.bind(this),
            kanban_compute_domain: this._computeDomain.bind(this),
            read_only_mode: this.read_only_mode,
            record: this._getQWebRecord(this.record),
            context: (this.state && this.state.context) ||
                (this.state && this.state.evalContext && this.state.evalContext.context) || {},
            user_context: this.getSession().user_context,
            widget: this,
        };
    },
    /**
     * Returns a safe record object for QWeb templates.
     * Missing fields fallback to {value:false, raw_value:false}.
     *
     * @private
     * @param {Object} record
     * @returns {Object}
     */
    _getQWebRecord: function (record) {
        var safeRecord = _.isObject(record) ? record : {};
        var makeEmptyField = function () {
            return {
                value: false,
                raw_value: false,
            };
        };

        _.each(_.keys(this.fields || {}), function (fieldName) {
            if (!safeRecord[fieldName]) {
                safeRecord[fieldName] = makeEmptyField();
            }
        });
        _.each(_.keys(this.fieldsInfo || {}), function (fieldName) {
            if (!safeRecord[fieldName]) {
                safeRecord[fieldName] = makeEmptyField();
            }
        });

        if (typeof Proxy !== 'function') {
            return safeRecord;
        }

        return new Proxy(safeRecord, {
            get: function (target, prop) {
                if (typeof prop === 'symbol') {
                    return target[prop];
                }
                if (prop in target) {
                    return target[prop];
                }
                return makeEmptyField();
            },
        });
    },
    /**
     * If an attribute `color` is set on the kanban record, adds the
     * corresponding color classname.
     *
     * @private
     */
    _setupColor: function () {
        var color_field = this.$el.attr('color');
        if (color_field && color_field in this.fields) {
            var colorHelp = _.str.sprintf(_t("Card color: %s"), this._getColorname(this.recordData[color_field]));
            var colorClass = this._getColorClassname(this.recordData[color_field]);
            this.$el.addClass(colorClass);
            this.$el.prepend('<span title="' + colorHelp + '" aria-label="' + colorHelp +'" role="img" class="oe_kanban_color_help"/>');
        }
    },
    /**
     * Renders the color picker in the kanban record, and binds the event handler
     *
     * @private
     */
    _setupColorPicker: function () {
        var $colorpicker = this.$('ul.oe_kanban_colorpicker');
        if (!$colorpicker.length) {
            return;
        }
        $colorpicker.html(QWeb.render('KanbanColorPicker'));
        $colorpicker.on('click', 'a', this._onColorChanged.bind(this));
    },
    /**
     * Builds an object containing the formatted record data used in the
     * template
     *
     * @private
     * @param {Object} recordData
     * @returns {Object} transformed record data
     */
    _transformRecord: function (recordData) {
        var self = this;
        recordData = recordData || {};
        var new_record = {};
        var fieldNames = _.isFunction(this.state.getFieldNames)
            ? this.state.getFieldNames()
            : _.union(
                _.keys(this.fields || {}),
                _.keys(this.fieldsInfo || {}),
                _.keys(recordData)
            );
        _.each(fieldNames, function (name) {
            var value = recordData[name];
            var r = _.clone(self.fields[name] || {});

            if ((r.type === 'date' || r.type === 'datetime') && value) {
                //  Multidados - Acerto na Formatação da Data e Data e Hora
                var dateValue = value;
                if (r.type === 'date' && value && value._i !== undefined) {
                    dateValue = moment(value._i).utc(false);
                }
                var dateObj = _.isFunction(dateValue.toDate) ? dateValue.toDate() : moment(dateValue).toDate();
                if (dateObj && !isNaN(dateObj.getTime())) {
                    r.moment = moment(dateObj);
                    r.raw_value = dateObj;
                } else {
                    r.raw_value = false;
                }
            } else if (r.type === 'one2many' || r.type === 'many2many') {
                var resIDs = value && _.isArray(value.res_ids) ? value.res_ids : [];
                r.raw_value = resIDs;
            } else if (r.type === 'many2one' ) {
                r.raw_value = value && value.res_id || false;
            } else {
                r.raw_value = value;
            }

            if (r.type) {
                var formatter = field_utils.format[r.type];
                if (formatter) {
                    var valueForFormatter = value;
                    if ((r.type === 'one2many' || r.type === 'many2many') && !valueForFormatter) {
                        valueForFormatter = {
                            data: [],
                            res_ids: [],
                            count: 0,
                        };
                    } else if (r.type === 'many2one' && !valueForFormatter) {
                        valueForFormatter = {
                            data: {
                                display_name: '',
                            },
                            res_id: false,
                        };
                    } else if ((r.type === 'date' || r.type === 'datetime') && valueForFormatter) {
                        if (!_.isFunction(valueForFormatter.format)) {
                            valueForFormatter = moment(valueForFormatter);
                        }
                    }
                    try {
                        if ((r.type === 'date' || r.type === 'datetime') && !valueForFormatter) {
                            r.value = false;
                        } else {
                            r.value = formatter(valueForFormatter, self.fields[name] || r, recordData, self.state);
                        }
                    } catch (e) {
                        r.value = r.raw_value !== undefined ? r.raw_value : false;
                    }
                } else {
                    r.value = value;
                }
            } else {
                r.value = value;
            }

            if (r.raw_value === undefined) {
                r.raw_value = false;
            }
            if (r.value === undefined) {
                r.value = false;
            }

            new_record[name] = r;
        });
        return new_record;
    },
    /**
     * Notifies the controller that the record has changed
     *
     * @private
     * @param {Object} data the new values
     */
    _updateRecord: function (data) {
        this.trigger_up('kanban_record_update', data);
    },

    //--------------------------------------------------------------------------
    // Handlers
    //--------------------------------------------------------------------------

    /**
     * @private
     * @param {MouseEvent} event
     */
    _onColorChanged: function (event) {
        event.preventDefault();
        var data = {};
        var color_field = $(event.delegateTarget).data('field') || 'color';
        data[color_field] = $(event.currentTarget).data('color');
        this.trigger_up('kanban_record_update', data);
    },
    /**
     * @private
     * @param {MouseEvent} event
     */
    _onGlobalClick: function (event) {
        if ($(event.target).parents('.o_dropdown_kanban').length) {
            return;
        }
        var trigger = true;
        var elem = event.target;
        var ischild = true;
        var children = [];
        while (elem) {
            var events = $._data(elem, 'events');
            if (elem === event.currentTarget) {
                ischild = false;
            }
            var test_event = events && events.click && (events.click.length > 1 || events.click[0].namespace !== 'bs.tooltip');
            var testLinkWithHref = elem.nodeName.toLowerCase() === 'a' && elem.href;
            if (ischild) {
                children.push(elem);
                if (test_event || testLinkWithHref) {
                    // Do not trigger global click if one child has a click
                    // event registered (or it is a link with href)
                    trigger = false;
                }
            }
            if (trigger && test_event) {
                _.each(events.click, function (click_event) {
                    if (click_event.selector) {
                        // For each parent of original target, check if a
                        // delegated click is bound to any previously found children
                        _.each(children, function (child) {
                            if ($(child).is(click_event.selector)) {
                                trigger = false;
                            }
                        });
                    }
                });
            }
            elem = elem.parentElement;
        }
        if (trigger) {
            this._openRecord();
        }
    },
    /**
     * @private
     * @param {MouseEvent} event
     */
    _onKanbanActionClicked: function (event) {
        event.preventDefault();

        var $action = $(event.currentTarget);
        var type = $action.data('type') || 'button';

        switch (type) {
            case 'edit':
                this.trigger_up('open_record', {id: this.db_id, mode: 'edit'});
                break;
            case 'open':
                this.trigger_up('open_record', {id: this.db_id});
                break;
            case 'delete':
                this.trigger_up('kanban_record_delete', {id: this.db_id, record: this});
                break;
            case 'action':
            case 'object':
                this.trigger_up('button_clicked', {
                    attrs: $action.data(),
                    record: this.state,
                });
                break;
            default:
                this.do_warn("Kanban: no action for type : " + type);
        }
    },
    /**
     * This event is linked to the kanban card when there is a global_click
     * class on this card
     *
     * @private
     * @param {KeyDownEvent} event
     */
    _onKeyDownCard: function (event) {
        switch (event.keyCode) {
            case $.ui.keyCode.ENTER:
                event.preventDefault();
                this._onGlobalClick(event);
                break;
        }
    },
    /**
     * This event is linked ot the kanban card when there is no global_click
     * class on the card
     *
     * @private
     * @param {KeyDownEvent} event
     */
    _onKeyDownOpenFirstLink: function (event) {
        switch (event.keyCode) {
            case $.ui.keyCode.ENTER:
                event.preventDefault();
                $(event.target).find('a, button').first().click();
                break;
        }
    },
    /**
     * Toggles the configuration panel of the record
     *
     * @private
     * @param {MouseEvent} event
     */
    _onManageTogglerClicked: function (event) {
        event.preventDefault();
        this.$el.toggleClass('o_dropdown_open');
        var colorClass = this._getColorClassname(this.recordData.color || 0);
        this.$('.o_kanban_manage_button_section').toggleClass(colorClass);
    },
});

return KanbanRecord;

});
