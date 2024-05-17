odoo.define('web.CalendarQuickCreate', function (require) {
"use strict";

var core = require('web.core');
var Dialog = require('web.Dialog');

var _t = core._t;
var QWeb = core.qweb;

/**
 * Quick creation view.
 *
 * Triggers a single event "added" with a single parameter "name", which is the
 * name entered by the user
 *
 * @class
 * @type {*}
 */
var QuickCreate = Dialog.extend({
    events: _.extend({}, Dialog.events, {
        'keyup input': '_onkeyup',
    }),

    /**
     * @constructor
     * @param {Widget} parent
     * @param {Object} buttons
     * @param {Object} options
     * @param {Object} dataTemplate
     * @param {Object} dataCalendar
     */
    init: function (parent, buttons, options, dataTemplate, dataCalendar) {
        this._buttons = buttons || false;
        this.options = options;

        // Can hold data pre-set from where you clicked on agenda
        this.dataTemplate = dataTemplate || {};
        this.dataCalendar = dataCalendar;

        var self = this;
        this._super(parent, {
            title: options.title,
            size: 'small',
            buttons: this._buttons ? [
                {text: _t("Create"), classes: 'btn-primary', click: function () {
                    if (!self._quickAdd(dataCalendar)) {
                        self.focus();
                    }
                }},
                {text: _t("Edit"), click: function () {
                    self._quickEdit(dataCalendar);
                }},
                {text: _t("Cancel"), close: true},
            ] : [],
            $content: QWeb.render('CalendarView.quick_create', {widget: this})
        });
    },

    //--------------------------------------------------------------------------
    // Public
    //--------------------------------------------------------------------------

    focus: function () {
        this.$('input').focus();
    },

    //--------------------------------------------------------------------------
    // Private
    //--------------------------------------------------------------------------

    /**
     * Gathers data from the quick create dialog a launch quick_create(data) method
     */
    _quickAdd: function (dataCalendar) {
        dataCalendar = $.extend({}, this.dataTemplate, dataCalendar);
        var val = this.$('input').val().trim();
        dataCalendar.title = val;
        return (val)? this.trigger_up('quickCreate', {data: dataCalendar, options: this.options}) : false;
    },

    /**
     * Adicionado pela Multidados:
     * - Foi somente transferido para uma função do objeto
     *   para possibilitar herança.
     *
     * Gathers data from the quick create dialog a launch quick_create(data) method
     * to edit form.
     */
    _quickEdit: function (dataCalendar) {
        $.extend(dataCalendar, {
            disableQuickCreate: true,
            title: this.$('input').val().trim(),
            on_save: this.destroy.bind(this),
        })
        this.trigger_up('openCreate', dataCalendar);
    },

    //--------------------------------------------------------------------------
    // Handlers
    //--------------------------------------------------------------------------

    /**
     * @private
     * @param {keyEvent} event
     */
    _onkeyup: function (event) {
        if (this._flagEnter) {
            return;
        }
        if(event.keyCode === $.ui.keyCode.ENTER) {
            this._flagEnter = true;
            if (!this._quickAdd(this.dataCalendar)){
                this._flagEnter = false;
            }
        } else if (event.keyCode === $.ui.keyCode.ESCAPE && this._buttons) {
            this.close();
        }
    },
});

return QuickCreate;

});
