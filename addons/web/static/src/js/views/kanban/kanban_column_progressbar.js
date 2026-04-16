odoo.define('web.KanbanColumnProgressBar', function (require) {
'use strict';

var Widget = require('web.Widget');
var session = require('web.session');
var utils = require('web.utils');

var KanbanColumnProgressBar = Widget.extend({
    template: 'KanbanView.ColumnProgressBar',
    events: {
        'click .o_kanban_counter_progress': '_onProgressBarParentClick',
        'click .progress-bar': '_onProgressBarClick',
    },
    /**
     * Allows to disable animations for tests.
     * @type {boolean}
     */
    ANIMATE: true,

    /**
     * @constructor
     */
    init: function (parent, options, columnState) {
        this._super.apply(this, arguments);

        this.columnID = options.columnID;
        this.columnState = columnState;
        this.progressBarValues = columnState.progressBarValues || {};

        // <progressbar/> attributes
        this.fieldName = this.progressBarValues.field || false;
        this.colors = this.progressBarValues.colors || {};
        this.sumField = this.progressBarValues.sum_field || false;
        this.progressBarHelp = this.progressBarValues.help || false;

        // Previous progressBar state
        var state = options.progressBarStates[this.columnID];
        if (state) {
            this.groupCount = state.groupCount;
            this.subgroupCounts = state.subgroupCounts;
            this.totalCounterValue = state.totalCounterValue;
            this.activeFilter = state.activeFilter;
        }

        // Prepare currency (TODO this should be automatic... use a field ?)
        var sumFieldInfo = this.sumField && columnState.fieldsInfo.kanban[this.sumField];
        var currencyField = sumFieldInfo && sumFieldInfo.options && sumFieldInfo.options.currency_field;
        if (currencyField && columnState.data.length) {
            var firstRecord = this._findFirstRecord(columnState.data);
            var currencyDP = firstRecord && firstRecord.data && firstRecord.data[currencyField];
            if (currencyDP && currencyDP.res_id && session.currencies[currencyDP.res_id]) {
                this.currency = session.currencies[currencyDP.res_id];
            }
        }
    },
    /**
     * @override
     */
    start: function () {
        var self = this;

        this.$bars = {};
        _.each(this.colors, function (val, key) {
            self.$bars[val] = self.$('.bg-' + val + '-full');
        });
        this.$counter = this.$('.o_kanban_counter_side');
        this.$number = this.$counter.find('b');

        if (this.currency) {
            var $currency = $('<span/>', {
                text: this.currency.symbol,
            });
            if (this.currency.position === 'before') {
                $currency.prependTo(this.$counter);
            } else {
                $currency.appendTo(this.$counter);
            }
        }

        return this._super.apply(this, arguments).then(function () {
            // This should be executed when the progressbar is fully rendered
            // and is in the DOM, this happens to be always the case with
            // current use of progressbars

            var subgroupCounts = {};
            var counts = self.progressBarValues.counts || {};
            _.each(self.colors, function (val, key) {
                var subgroupCount = counts[key] || 0;
                if (self.activeFilter === key && subgroupCount === 0) {
                    self.activeFilter = false;
                }
                subgroupCounts[key] = subgroupCount;
            });

            self.groupCount = self.columnState.count;
            self.subgroupCounts = subgroupCounts;
            self.prevTotalCounterValue = self.totalCounterValue;
            self.totalCounterValue = self.sumField ? (self.columnState.aggregateValues[self.sumField] || 0) : self.columnState.count;
            self._notifyState();
            self._render();
        });
    },

    //--------------------------------------------------------------------------
    // Private
    //--------------------------------------------------------------------------

    /**
     * Updates the rendering according to internal data. This is done without
     * qweb rendering because there are animations.
     *
     * @private
     */
    _render: function () {
        var self = this;

        // Update column display according to active filter
        this.trigger_up('tweak_column', {
            callback: function ($el) {
                $el.removeClass('o_kanban_group_show');
                _.each(self.colors, function (val, key) {
                    $el.removeClass('o_kanban_group_show_' + val);
                });
                if (self.activeFilter) {
                    $el.addClass('o_kanban_group_show o_kanban_group_show_' + self.colors[self.activeFilter]);
                }
            },
        });
        this.trigger_up('tweak_column_records', {
            callback: function ($el, recordData) {
                var categoryValue = recordData[self.fieldName];
                _.each(self.colors, function (val, key) {
                    $el.removeClass('oe_kanban_card_' + val);
                });
                if (self.colors[categoryValue]) {
                    $el.addClass('oe_kanban_card_' + self.colors[categoryValue]);
                }
            },
        });
        if (this.progressBarHelp && _.every(this.subgroupCounts, function (val) { return val === 0; })) {
            this.$el.tooltip({
                delay: 0,
                trigger: 'hover',
                title: this.progressBarHelp,
            });
        }

        // Display and animate the progress bars
        var barNumber = 0;
        var barMinWidth = 6; // In %
        _.each(self.colors, function (val, key) {
            var $bar = self.$bars[val];
            var count = self.subgroupCounts && self.subgroupCounts[key] || 0;

            if (!$bar) {
                return;
            }

            // Adapt tooltip
            $bar.attr('data-original-title', count + ' ' + key);
            $bar.tooltip({
                delay: 0,
                trigger: 'hover',
            });

            // Adapt active state
            $bar.toggleClass('progress-bar-animated progress-bar-striped', key === self.activeFilter);

            // Adapt width
            $bar.removeClass('o_bar_has_records transition-off');
            window.getComputedStyle($bar[0]).getPropertyValue('width'); // Force reflow so that animations work
            if (count > 0) {
                $bar.addClass('o_bar_has_records');
                // Make sure every bar that has records has some space
                // and that everything adds up to 100%
                var maxWidth = 100 - barMinWidth * barNumber;
                self.$('.progress-bar.o_bar_has_records').css('max-width', maxWidth + '%');
                $bar.css('width', (count * 100 / self.groupCount) + '%');
                barNumber++;
                $bar.attr('aria-valuemin', 0);
                $bar.attr('aria-valuemax', self.groupCount);
                $bar.attr('aria-valuenow', count);
            } else {
                $bar.css('width', '');
            }
        });
        this.$('.progress-bar.o_bar_has_records').css('min-width', barMinWidth + '%');

        // Display and animate the counter number
        var start = this.prevTotalCounterValue;
        var end = this.totalCounterValue;

        if (this.activeFilter) {
            if (this.sumField) {
                end = this._sumFilteredValue(self.columnState.data, this.activeFilter);
            } else {
                end = this.subgroupCounts[this.activeFilter];
            }
        }
        this.prevTotalCounterValue = end;
        var animationClass = start > 999 ? 'o_kanban_grow' : 'o_kanban_grow_huge';

        if (start !== undefined && (end > start || this.activeFilter) && this.ANIMATE) {
            $({currentValue: start}).animate({currentValue: end}, {
                duration: 1000,
                start: function () {
                    self.$counter.addClass(animationClass);
                },
                step: function () {
                    self.$number.html(_getCounterHTML(this.currentValue));
                },
                complete: function () {
                    self.$number.html(_getCounterHTML(this.currentValue));
                    self.$counter.removeClass(animationClass);
                },
            });
        } else {
            this.$number.html(_getCounterHTML(end));
        }

        function _getCounterHTML(value) {
            return utils.human_number(value, 0, 3);
        }
    },
    /**
     * Notifies the new progressBar state so that if a full rerender occurs, the
     * new progressBar that would replace this one will be initialized with
     * current state, so that animations are correct.
     *
     * @private
     */
    _notifyState: function () {
        this.trigger_up('set_progress_bar_state', {
            columnID: this.columnID,
            values: {
                groupCount: this.groupCount,
                subgroupCounts: this.subgroupCounts,
                totalCounterValue: this.totalCounterValue,
                activeFilter: this.activeFilter,
            },
        });
    },
    /**
     * Returns the first record found in a nested list (records/subgroups).
     *
     * @private
     * @param {Object[]} data
     * @returns {Object|undefined}
     */
    _findFirstRecord: function (data) {
        var result;
        _.some(data || [], function (item) {
            if (!item) {
                return false;
            }
            if (item.type === 'record') {
                result = item;
                return true;
            }
            if (item.type === 'list' && item.data && item.data.length) {
                result = this._findFirstRecord(item.data);
                return !!result;
            }
            return false;
        }, this);
        return result;
    },
    /**
     * Sums current measure for a given active filter in nested data.
     *
     * @private
     * @param {Object[]} data
     * @param {string} activeFilter
     * @returns {number}
     */
    _sumFilteredValue: function (data, activeFilter) {
        var self = this;
        var total = 0;
        _.each(data || [], function (item) {
            if (!item) {
                return;
            }
            if (item.type === 'list') {
                total += self._sumFilteredValue(item.data, activeFilter);
                return;
            }
            var recordData = item.data || {};
            if (activeFilter === recordData[self.fieldName]) {
                total += parseFloat(recordData[self.sumField]) || 0;
            }
        });
        return total;
    },

    //--------------------------------------------------------------------------
    // Handlers
    //--------------------------------------------------------------------------

    /**
     * @private
     * @param {Event} ev
     */
    _onProgressBarClick: function (ev) {
        this.$clickedBar = $(ev.currentTarget);
        var filter = this.$clickedBar.data('filter');
        this.activeFilter = (this.activeFilter === filter ? false : filter);
        this._notifyState();
        this._render();
    },
    /**
     * @private
     * @param {Event} ev
     */
    _onProgressBarParentClick: function (ev) {
        if (ev.target !== ev.currentTarget) {
            return;
        }
        this.activeFilter = false;
        this._notifyState();
        this._render();
    },
});
return KanbanColumnProgressBar;
});
