odoo.define('web.BasicRenderer', function (require) {
"use strict";

/**
 * The BasicRenderer is an abstract class designed to share code between all
 * views that uses a BasicModel. The main goal is to keep track of all field
 * widgets, and properly destroy them whenever a rerender is done. The widgets
 * and modifiers updates mechanism is also shared in the BasicRenderer.
 */
var AbstractRenderer = require('web.AbstractRenderer');
var config = require('web.config');
var core = require('web.core');
var dom = require('web.dom');
var widgetRegistry = require('web.widget_registry');
var Domain = require('web.Domain');
var qweb = core.qweb;

var BasicRenderer = AbstractRenderer.extend({
    custom_events: {
        navigation_move: '_onNavigationMove',
    },
    /**
     * Basic renderers implements the concept of "mode", they can either be in
     * readonly mode or editable mode.
     *
     * @override
     */
    init: function (parent, state, params) {
        this._super.apply(this, arguments);
        this.activeActions = params.activeActions;
        this.viewType = params.viewType;
        this.mode = params.mode || 'readonly';
        this.widgets = [];
    },
    /**
     * This method has two responsabilities: find every invalid fields in the
     * current view, and making sure that they are displayed as invalid, by
     * toggling the o_form_invalid css class. It has to be done both on the
     * widget, and on the label, if any.
     *
     * @param {string} recordID
     * @returns {string[]} the list of invalid field names
     */
    canBeSaved: function (recordID) {
        var self = this;
        var invalidFields = [];
        _.each(this.allFieldWidgets[recordID], function (widget) {
            var canBeSaved = self._canWidgetBeSaved(widget);
            if (!canBeSaved) {
                invalidFields.push(widget.name);
            }
            if (widget.el) { // widget may not be started yet
                widget.$el.toggleClass('o_field_invalid', !canBeSaved);
                widget.$el.attr('aria-invalid', !canBeSaved);
            }
        });
        return invalidFields;
    },
    /**
     * Calls 'commitChanges' on all field widgets, so that they can notify the
     * environment with their current value (useful for widgets that can't
     * detect when their value changes or that have to validate their changes
     * before notifying them).
     *
     * @param {string} recordID
     * @return {Deferred}
     */
    commitChanges: function (recordID) {
        var defs = _.map(this.allFieldWidgets[recordID], function (widget) {
            return widget.commitChanges();
        });
        return $.when.apply($, defs);
    },
    /**
     * Updates the internal state of the renderer to the new state. By default,
     * this also implements the recomputation of the modifiers and their
     * application to the DOM and the reset of the field widgets if needed.
     *
     * In case the given record is not found anymore, a whole re-rendering is
     * completed (possible if a change in a record caused an onchange which
     * erased the current record).
     *
     * We could always rerender the view from scratch, but then it would not be
     * as efficient, and we might lose some local state, such as the input focus
     * cursor, or the scrolling position.
     *
     * @param {Object} state
     * @param {string} id
     * @param {string[]} fields
     * @param {OdooEvent} ev
     * @returns {Deferred<AbstractField[]>} resolved with the list of widgets
     *                                      that have been reset
     */
    confirmChange: function (state, id, fields, ev) {
        this.state = state;

        var record = state.id === id ? state : _.findWhere(state.data, {id: id});
        if (!record) {
            return this._render().then(_.constant([]));
        }

        // reset all widgets (from the <widget> tag) if any:
        _.invoke(this.widgets, 'updateState', state);

        var defs = [];

        // Reset all the field widgets that are marked as changed and the ones
        // which are configured to always be reset on any change
        var resetWidgets = [];
        _.each(this.allFieldWidgets[id], function (widget) {
            var fieldChanged = _.contains(fields, widget.name);
            if (fieldChanged || widget.resetOnAnyFieldChange) {
                defs.push(widget.reset(record, ev, fieldChanged));
                resetWidgets.push(widget);
            }
        });

        // The modifiers update is done after widget resets as modifiers
        // associated callbacks need to have all the widgets with the proper
        // state before evaluation
        defs.push(this._updateAllModifiers(record));

        return $.when.apply($, defs).then(function () {
            return resetWidgets;
        });
    },
    /**
     * Activates the widget and move the cursor to the given offset
     *
     * @param {string} id
     * @param {string} fieldName
     * @param {integer} offset
     */
    focusField: function (id, fieldName, offset) {
        this.editRecord(id);
        if (typeof offset === "number") {
            var field = _.findWhere(this.allFieldWidgets[id], {name: fieldName});
            dom.setSelectionRange(field.getFocusableElement().get(0), {start: offset, end: offset});
        }
    },
    //--------------------------------------------------------------------------
    // Private
    //--------------------------------------------------------------------------

    /**
     * Add a tooltip on a $node, depending on a field description
     *
     * @param {FieldWidget} widget
     * @param {$node} $node
     */
    _addFieldTooltip: function (widget, $node) {
        // optional argument $node, the jQuery element on which the tooltip
        // should be attached if not given, the tooltip is attached on the
        // widget's $el
        $node = $node.length ? $node : widget.$el;
        if ($node && 'tooltip' in $node) {
            $node.tooltip({
                title: function () {
                    return qweb.render('WidgetLabel.tooltip', {
                        debug: config.debug,
                        widget: widget,
                    });
                }
            });
        }
    },
    /**
     * Activates the widget at the given index for the given record if possible
     * or the "next" possible one. Usually, a widget can be activated if it is
     * in edit mode, and if it is visible.
     *
     * @private
     * @param {Object} record
     * @param {integer} currentIndex
     * @param {Object} [options={}]
     * @param {integer} [options.inc=1] - the increment to use when searching for the
     *   "next" possible one
     * @param {boolean} [options.noAutomaticCreate=false]
     * @param {boolean} [options.wrap=false] if true, when we arrive at the end of the
     *   list of widget, we wrap around and try to activate widgets starting at
     *   the beginning. Otherwise, we just stop trying and return -1
     * @returns {integer} the index of the widget that was activated or -1 if
     *   none was possible to activate
     */
    _activateFieldWidget: function (record, currentIndex, options) {
        options = options || {};
        _.defaults(options, {inc: 1, wrap: false});
        currentIndex = Math.max(0,currentIndex); // do not allow negative currentIndex

        var recordWidgets = this.allFieldWidgets[record.id] || [];
        for (var i = 0 ; i < recordWidgets.length ; i++) {
            var activated = recordWidgets[currentIndex].activate(
                {
                    event: options.event,
                    noAutomaticCreate: options.noAutomaticCreate || false
                });
            if (activated) {
                return currentIndex;
            }

            currentIndex += options.inc;
            if (currentIndex >= recordWidgets.length) {
                if (options.wrap) {
                    currentIndex -= recordWidgets.length;
                } else {
                    return -1;
                }
            } else if (currentIndex < 0) {
                if (options.wrap) {
                    currentIndex += recordWidgets.length;
                } else {
                    return -1;
                }
            }
        }
        return -1;
    },
    /**
     * This is a wrapper of the {@see _activateFieldWidget} function to select
     * the next possible widget instead of the given one.
     *
     * @private
     * @param {Object} record
     * @param {integer} currentIndex
     * @param {Object|undefined} options
     * @return {integer}
     */
    _activateNextFieldWidget: function (record, currentIndex, options) {
        currentIndex = (currentIndex + 1) % (this.allFieldWidgets[record.id] || []).length;
        return this._activateFieldWidget(record, currentIndex, _.extend({inc: 1}, options));
    },
    /**
     * This is a wrapper of the {@see _activateFieldWidget} function to select
     * the previous possible widget instead of the given one.
     *
     * @private
     * @param {Object} record
     * @param {integer} currentIndex
     * @return {integer}
     */
    _activatePreviousFieldWidget: function (record, currentIndex) {
        currentIndex = currentIndex ? (currentIndex - 1) : ((this.allFieldWidgets[record.id] || []).length - 1);
        return this._activateFieldWidget(record, currentIndex, {inc:-1});
    },
    /**
     * Does the necessary DOM updates to match the given modifiers data. The
     * modifiers data is supposed to contain the properly evaluated modifiers
     * associated to the given records and elements.
     *
     * @param {Object} modifiersData
     * @param {Object} record
     * @param {Object} [element] - do the update only on this element if given
     */
    _applyModifiers: function (modifiersData, record, element) {
        var self = this;
        var modifiers = modifiersData.evaluatedModifiers[record.id] || {};

        if (element) {
            _apply(element);
        } else {
            // Clone is necessary as the list might change during _.each
            _.each(_.clone(modifiersData.elementsByRecord[record.id]), _apply);
        }

        function _apply(element) {
            // If the view is in edit mode and that a widget have to switch
            // its "readonly" state, we have to re-render it completely
            if ('readonly' in modifiers && element.widget) {
                var mode = modifiers.readonly ? 'readonly' : modifiersData.baseModeByRecord[record.id];
                if (mode !== element.widget.mode) {
                    self._rerenderFieldWidget(element.widget, record, {
                        keepBaseMode: true,
                        mode: mode,
                    });
                    return; // Rerendering already applied the modifiers, no need to go further
                }
            }

            // Toggle modifiers CSS classes if necessary
            element.$el.toggleClass("o_invisible_modifier", !!modifiers.invisible);
            element.$el.toggleClass("o_readonly_modifier", !!modifiers.readonly);
            element.$el.toggleClass("o_required_modifier", !!modifiers.required);

            if (element.widget && element.widget.updateModifiersValue) {
                element.widget.updateModifiersValue(modifiers);
            }

            // Call associated callback
            if (element.callback) {
                element.callback(element, modifiers, record);
            }
        }
    },
    /**
     * Determines if a given field widget value can be saved. For this to be
     * true, the widget must be valid (properly parsed value) and have a value
     * if the associated view field is required.
     *
     * @private
     * @param {AbstractField} widget
     * @returns {boolean|Deferred<boolean>} @see AbstractField.isValid
     */
    _canWidgetBeSaved: function (widget) {
        var modifiers = this._getEvaluatedModifiers(widget.__node, widget.record);
        return widget.isValid() && (widget.isSet() || !modifiers.required);
    },
    /**
     * Destroys a given widget associated to the given record and removes it
     * from internal referencing.
     *
     * @private
     * @param {string} recordID id of the local resource
     * @param {AbstractField} widget
     * @returns {integer} the index of the removed widget
     */
    _destroyFieldWidget: function (recordID, widget) {
        var recordWidgets = this.allFieldWidgets[recordID];
        var index = recordWidgets.indexOf(widget);
        if (index >= 0) {
            recordWidgets.splice(index, 1);
        }
        this._unregisterModifiersElement(widget.__node, recordID, widget);
        widget.destroy();
        return index;
    },
    /**
     * Searches for the last evaluation of the modifiers associated to the given
     * data (modifiers evaluation are supposed to always be up-to-date as soon
     * as possible).
     *
     * @private
     * @param {Object} node
     * @param {Object} record
     * @returns {Object} the evaluated modifiers associated to the given node
     *                   and record (not recomputed by the call)
     */
    _getEvaluatedModifiers: function (node, record) {
        var element = this._getModifiersData(node);
        if (!element) {
            return {};
        }
        return element.evaluatedModifiers[record.id] || {};
    },
    /**
     * Searches through the registered modifiers data for the one which is
     * related to the given node.
     *
     * @private
     * @param {Object} node
     * @returns {Object|undefined} related modifiers data if any
     *                             undefined otherwise
     */
    _getModifiersData: function (node) {
        return _.findWhere(this.allModifiersData, {node: node});
    },
    /**
     * @private
     * @param {jQueryElement} $el
     * @param {Object} node
     */
    _handleAttributes: function ($el, node) {
        if ($el.is('button')) {
            return;
        }
        if (node.attrs.class) {
            $el.addClass(node.attrs.class);
        }
        if (node.attrs.style) {
            $el.attr('style', node.attrs.style);
        }
    },
    /**
     * Used by list and kanban renderers to determine whether or not to display
     * the no content helper (if there is no data in the state to display)
     *
     * @private
     * @returns {boolean}
     */
    _hasContent: function () {
        return this.state.count !== 0;
    },
    /**
     * This function is called each time a field widget is created, when it is
     * ready (after its willStart and Start methods are complete).  This is the
     * place where work having to do with $el should be done.
     *
     * @private
     * @param {Widget} widget the field widget instance
     * @param {Object} node the attrs coming from the arch
     */
    _postProcessField: function (widget, node) {
    },
    /**
     * Registers or updates the modifiers data associated to the given node.
     * This method is quiet complex as it handles all the needs of the basic
     * renderers:
     *
     * - On first registration, the modifiers are evaluated thanks to the given
     *   record. This allows nodes that will produce an AbstractField instance
     *   to have their modifiers registered before this field creation as we
     *   need the readonly modifier to be able to instantiate the AbstractField.
     *
     * - On additional registrations, if the node was already registered but the
     *   record is different, we evaluate the modifiers for this record and
     *   saves them in the same object (without reparsing the modifiers).
     *
     * - On additional registrations, the modifiers are not reparsed (or
     *   reevaluated for an already seen record) but the given widget or DOM
     *   element is associated to the node modifiers.
     *
     * - The new elements are immediately adapted to match the modifiers and the
     *   given associated callback is called even if there is no modifiers on
     *   the node (@see _applyModifiers). This is indeed necessary as the
     *   callback is a description of what to do when a modifier changes. Even
     *   if there is no modifiers, this action must be performed on first
     *   rendering to avoid code duplication. If there is no modifiers, they
     *   will however not be registered for modifiers updates.
     *
     * - When a new element is given, it does not replace the old one, it is
     *   added as an additional element. This is indeed useful for nodes that
     *   will produce multiple DOM (as a list cell and its internal widget or
     *   a form field and its associated label).
     *   (@see _unregisterModifiersElement for removing an associated element.)
     *
     * Note: also on view rerendering, all the modifiers are forgotten so that
     * the renderer only keeps the ones associated to the current DOM state.
     *
     * @private
     * @param {Object} node
     * @param {Object} record
     * @param {jQuery|AbstractField} [element]
     * @param {Object} [options]
     * @param {Object} [options.callback] the callback to call on registration
     *   and on modifiers updates
     * @param {boolean} [options.keepBaseMode=false] this function registers the
     *   'baseMode' of the node on a per record basis;
     *   this is a field widget specific settings which
     *   represents the generic mode of the widget, regardless of its modifiers
     *   (the interesting case is the list view: all widgets are supposed to be
     *   in the baseMode 'readonly', except the ones that are in the line that
     *   is currently being edited).
     *   With option 'keepBaseMode' set to true, the baseMode of the record's
     *   node isn't overridden (this is particularily useful when a field widget
     *   is re-rendered because its readonly modifier changed, as in this case,
     *   we don't want to change its base mode).
     * @param {string} [options.mode] the 'baseMode' of the record's node is set to this
     *   value (if not given, it is set to this.mode, the mode of the renderer)
     * @returns {Object} for code efficiency, returns the last evaluated
     *   modifiers for the given node and record.
     */
    _registerModifiers: function (node, record, element, options) {
        options = options || {};
        // Check if we already registered the modifiers for the given node
        // If yes, this is simply an update of the related element
        // If not, check the modifiers to see if it needs registration
        var modifiersData = this._getModifiersData(node);
        if (!modifiersData) {
            var modifiers = node.attrs.modifiers || {};
            modifiersData = {
                node: node,
                modifiers: modifiers,
                evaluatedModifiers: {},
                elementsByRecord: {},
                baseModeByRecord : {},
            };
            if (!_.isEmpty(modifiers)) { // Register only if modifiers might change (TODO condition might be improved here)
                this.allModifiersData.push(modifiersData);
            }
        }

        // Compute the record's base mode
        if (!modifiersData.baseModeByRecord[record.id] || !options.keepBaseMode) {
            modifiersData.baseModeByRecord[record.id] = options.mode || this.mode;
        }

        // Evaluate if necessary
        if (!modifiersData.evaluatedModifiers[record.id]) {
            modifiersData.evaluatedModifiers[record.id] = record.evalModifiers(modifiersData.modifiers);
        }

        // Element might not be given yet (a second call to the function can
        // update the registration with the element)
        if (element) {
            var newElement = {};
            if (element instanceof jQuery) {
                newElement.$el = element;
            } else {
                newElement.widget = element;
                newElement.$el = element.$el;
            }
            if (options && options.callback) {
                newElement.callback = options.callback;
            }

            if (!modifiersData.elementsByRecord[record.id]) {
                modifiersData.elementsByRecord[record.id] = [];
            }
            modifiersData.elementsByRecord[record.id].push(newElement);

            this._applyModifiers(modifiersData, record, newElement, options);
        }

        return modifiersData.evaluatedModifiers[record.id];
    },
    /**
     * Render the view
     *
     * @override
     * @returns {Deferred}
     */
    _render: function () {
        var oldAllFieldWidgets = this.allFieldWidgets;
        this.allFieldWidgets = {}; // TODO maybe merging allFieldWidgets and allModifiersData into "nodesData" in some way could be great
        this.allModifiersData = [];
        return this._renderView().then(function () {
            _.each(oldAllFieldWidgets, function (recordWidgets) {
                _.each(recordWidgets, function (widget) {
                    widget.destroy();
                });
            });
        });
    },
    /**
     * Renders a button according to a given node element.
     *
     * @private
     * @param {Object} node
     * @param {Object} [options]
     * @param {string} [options.extraClass]
     * @param {boolean} [options.textAsTitle=false]
     * @returns {jQuery}
     */
    _renderButtonFromNode: function (node, options) {
        var btnOptions = {
            attrs: _.omit(node.attrs, 'icon', 'string', 'type', 'attrs', 'modifiers', 'options'),
            icon: node.attrs.icon,
        };
        if (options && options.extraClass) {
            var classes = btnOptions.attrs.class ? btnOptions.attrs.class.split(' ') : [];
            btnOptions.attrs.class = _.uniq(classes.concat(options.extraClass.split(' '))).join(' ');
        }
        var str = (node.attrs.string || '').replace(/_/g, '');
        if (str) {
            if (options && options.textAsTitle) {
                btnOptions.attrs.title = str;
            } else {
                btnOptions.text = str;
            }
        }
        return dom.renderButton(btnOptions);
    },
    /**
     * Instantiates the appropriate AbstractField specialization for the given
     * node and prepares its rendering and addition to the DOM. Indeed, the
     * rendering of the widget will be started and the associated deferred will
     * be added to the 'defs' attribute. This is supposed to be created and
     * deleted by the calling code if necessary.
     *
     * Note: we always return a $el.  If the field widget is asynchronous, this
     * $el will be replaced by the real $el, whenever the widget is ready (start
     * method is done).  This means that this is not the correct place to make
     * changes on the widget $el.  For this, @see _postProcessField method
     *
     * @private
     * @param {Object} node
     * @param {Object} record
     * @param {Object} [options] passed to @_registerModifiers
     * @param {string} [options.mode] either 'edit' or 'readonly' (defaults to
     *   this.mode, the mode of the renderer)
     * @returns {jQueryElement}
     */
    _renderFieldWidget: function (node, record, options) {
        options = options || {};
        var self = this;
        var fieldName = node.attrs.name;
        // Register the node-associated modifiers
        var mode = options.mode || this.mode;
        var modifiers = this._registerModifiers(node, record, null, options);
        // Initialize and register the widget
        // Readonly status is known as the modifiers have just been registered
        var Widget = record.fieldsInfo[this.viewType][fieldName].Widget;
        var widget = new Widget(this, fieldName, record, {
            mode: modifiers.readonly ? 'readonly' : mode,
            viewType: this.viewType,
        });
        
        // Obtém as opções do widget, incluindo regras de string dinâmica
        // Obs.: Caso não atenda os domains, o campo irá manter a string original
        //
        // Exemplo de uso:
        // <field name="project_id"
        //        options="{'dynamic_string': [{'domain': [('contract_type_control', '=', 'out_invoice')], 'string': 'Project'},
        //                                     {'domain': [('contract_type_control', 'in', ['in_invoice', 'hr'])], 'string': 'Analytic Account'}]}"                                   
        //
        var options = widget.nodeOptions || {};
        var dynamicStringRules = options.dynamic_string || null;

        if (dynamicStringRules) {
            // Filtra as regras de string dinâmica que atendem às condições definidas
            var dynamicLabel = _.chain(dynamicStringRules).filter(function (rule) {
                    return self._evaluateCondition(rule, self.state.data);
                }).find(function (rule) {
                    return _t(rule.string);
                }).get('string', null).value();

            // Atualiza o label do campo caso uma string dinâmica seja encontrada
            if (dynamicLabel) { 
                widget.field.string = dynamicLabel;
            }
        }

        // Register the widget so that it can easily be found again
        if (this.allFieldWidgets[record.id] === undefined) {
            this.allFieldWidgets[record.id] = [];
        }
        this.allFieldWidgets[record.id].push(widget);

        widget.__node = node; // TODO get rid of this if possible one day

        // Prepare widget rendering and save the related deferred
        var def = widget._widgetRenderAndInsert(function () {});
        var async = def.state() === 'pending';
        var $el = async ? $('<div>') : widget.$el;
        if (async) {
            this.defs.push(def);
        }

        // Update the modifiers registration by associating the widget and by
        // giving the modifiers options now (as the potential callback is
        // associated to new widget)
        var self = this;
        def.then(function () {
            if (async) {
                $el.replaceWith(widget.$el);
            }
            self._registerModifiers(node, record, widget, {
                callback: function (element, modifiers, record) {
                    element.$el.toggleClass('o_field_empty', !!(
                        record.data.id
                        && (modifiers.readonly || mode === 'readonly')
                        && !element.widget.isSet()
                    ));
                },
                keepBaseMode: !!options.keepBaseMode,
                mode: mode,
            });
            self._postProcessField(widget, node);
        });

        return $el;
    },
    /**
     * Avalia uma condição definida em dynamic_string.
     * @param {Object} rule - Regra no formato {field, value, string}.
     * @param {Object} recordData - Dados do registro atual.
     * @returns {Boolean} - Retorna true se a condição for atendida, caso contrário false.
     */
    _evaluateCondition: function (rule, recordData) {
        if (!rule || !rule.domain || !rule.string) {
            return false; // Regra inválida
        }
        return new Domain((rule.domain || []), recordData).compute(recordData);
    },     
    /**
     * Renders the nocontent helper.
     *
     * This method is a helper for renderers that want to display a help
     * message when no content is available.
     *
     * @private
     * @returns {jQueryElement}
     */
    _renderNoContentHelper: function () {
        var $noContent =
            $('<div>').html(this.noContentHelp).addClass('o_nocontent_help');
        return $('<div>')
            .addClass('o_view_nocontent')
            .append($noContent);
    },
    /**
     * Actual rendering. Supposed to be overridden by concrete renderers.
     * The basic responsabilities of _renderView are:
     * - use the xml arch of the view to render a jQuery representation
     * - instantiate a widget from the registry for each field in the arch
     *
     * Note that the 'state' field should contains all necessary information
     * for the rendering. The field widgets should be as synchronous as
     * possible.
     *
     * @abstract
     * @returns {Deferred}
     */
    _renderView: function () {
        return $.when();
    },
    /**
     * Instantiate custom widgets
     *
     * @private
     * @param {Object} record
     * @param {Object} node
     * @returns {jQueryElement}
     */
    _renderWidget: function (record, node) {
        var Widget = widgetRegistry.get(node.attrs.name);
        var widget = new Widget(this, record, node);

        this.widgets.push(widget);

        // Prepare widget rendering and save the related deferred
        var def = widget._widgetRenderAndInsert(function () {});
        var async = def.state() === 'pending';
        if (async) {
            this.defs.push(def);
        }
        var $el = async ? $('<div>') : widget.$el;

        var self = this;
        def.then(function () {
            self._handleAttributes(widget.$el, node);
            self._registerModifiers(node, record, widget);
            widget.$el.addClass('o_widget');
            if (async) {
                $el.replaceWith(widget.$el);
            }
        });

        return $el;
    },

    /**
     * Rerenders a given widget and make sure the associated data which
     * referenced the old one is updated.
     *
     * @private
     * @param {Widget} widget
     * @param {Object} record
     * @param {Object} [options] options passed to @_renderFieldWidget
     */
    _rerenderFieldWidget: function (widget, record, options) {
        // Render the new field widget
        var $el = this._renderFieldWidget(widget.__node, record, options);
        widget.$el.replaceWith($el);

        // Destroy the old widget and position the new one at the old one's
        var oldIndex = this._destroyFieldWidget(record.id, widget);
        var recordWidgets = this.allFieldWidgets[record.id];
        var newWidget = recordWidgets.pop();
        recordWidgets.splice(oldIndex, 0, newWidget);
    },
    /**
     * Unregisters an element of the modifiers data associated to the given
     * node and record.
     *
     * @param {Object} node
     * @param {string} recordID id of the local resource
     * @param {jQuery|AbstractField} element
     */
    _unregisterModifiersElement: function (node, recordID, element) {
        var modifiersData = this._getModifiersData(node);
        if (modifiersData) {
            var elements = modifiersData.elementsByRecord[recordID];
            var index = _.findIndex(elements, function (oldElement) {
                return oldElement.widget === element
                    || oldElement.$el[0] === element[0];
            });
            if (index >= 0) {
                elements.splice(index, 1);
            }
        }
    },
    /**
     * Does two actions, for each registered modifiers:
     * 1) Recomputes the modifiers associated to the given record and saves them
     *    (as boolean values) in the appropriate modifiers data.
     * 2) Updates the rendering of the view elements associated to the given
     *    record to match the new modifiers.
     *
     * @see _applyModifiers
     *
     * @private
     * @param {Object} record
     * @returns {Deferred} resolved once finished
     */
    _updateAllModifiers: function (record) {
        var self = this;

        var defs = [];
        this.defs = defs; // Potentially filled by widget rerendering
        _.each(this.allModifiersData, function (modifiersData) {
            modifiersData.evaluatedModifiers[record.id] = record.evalModifiers(modifiersData.modifiers);
            self._applyModifiers(modifiersData, record);
        });
        delete this.defs;

        return $.when.apply($, defs);
    },

    //--------------------------------------------------------------------------
    // Handlers
    //--------------------------------------------------------------------------

    /**
     * When someone presses the TAB/UP/DOWN/... key in a widget, it is nice to
     * be able to navigate in the view (default browser behaviors are disabled
     * by Odoo).
     *
     * @abstract
     * @private
     * @param {OdooEvent} ev
     */
    _onNavigationMove: function (ev) {},
});

return BasicRenderer;
});
