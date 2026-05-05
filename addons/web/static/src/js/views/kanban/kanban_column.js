odoo.define('web.KanbanColumn', function (require) {
"use strict";

var config = require('web.config');
var core = require('web.core');
var Dialog = require('web.Dialog');
var KanbanRecord = require('web.KanbanRecord');
var RecordQuickCreate = require('web.kanban_record_quick_create');
var view_dialogs = require('web.view_dialogs');
var viewUtils = require('web.viewUtils');
var Widget = require('web.Widget');
var KanbanColumnProgressBar = require('web.KanbanColumnProgressBar');

var _t = core._t;
var QWeb = core.qweb;

/**
 * Documentacao PT-BR (customizacoes principais):
 * - Renderiza subgrupos aninhados dentro de cada coluna do kanban.
 * - Mantem estado visual aberto/fechado baseado em `isOpen` do model.
 * - Faz lazy-load de subgrupo ao expandir quando ha contador sem cards carregados.
 */

var KanbanColumn = Widget.extend({
    template: 'KanbanView.Group',
    custom_events: {
        cancel_quick_create: '_onCancelQuickCreate',
        quick_create_add_record: '_onQuickCreateAddRecord',
        tweak_column: '_onTweakColumn',
        tweak_column_records: '_onTweakColumnRecords',
    },
    events: {
        'click .o_column_edit': '_onEditColumn',
        'click .o_column_delete': '_onDeleteColumn',
        'click .o_kanban_quick_add': '_onAddQuickCreate',
        'click .o_kanban_load_more': '_onLoadMore',
        'click .o_kanban_toggle_fold': '_onToggleFold',
        'click .o_kanban_subgroup_header': '_onToggleSubGroup',
        'keydown .o_kanban_subgroup_header': '_onSubGroupHeaderKeyDown',
        'click .o_column_archive_records': '_onArchiveRecords',
        'click .o_column_unarchive_records': '_onUnarchiveRecords'
    },
    /**
     * @override
     */
    init: function (parent, data, options, recordOptions) {
        this._super(parent);
        this.db_id = data.id;
        this.data_records = data.data;
        this.data = data;

        var value = data.value;
        this.id = data.res_id;
        this.folded = !data.isOpen;
        this.has_active_field = 'active' in data.fields;
        this.fields = data.fields;
        this.records = [];
        this.modelName = data.model;

        this.quick_create = options.quick_create;
        this.quickCreateView = options.quickCreateView;
        this.groupedBy = options.groupedBy;
        this.grouped_by_m2o = options.grouped_by_m2o;
        this.editable = options.editable;
        this.deletable = options.deletable;
        this.archivable = options.archivable;
        this.draggable = options.draggable;
        this.group_readonly = options.group_readonly || [];
        this.KanbanRecord = options.KanbanRecord || KanbanRecord; // the KanbanRecord class to use
        this.records_editable = options.records_editable;
        this.records_deletable = options.records_deletable;
        this.relation = options.relation;
        this.showLevelTotals = !!options.show_level_totals;
        this.offset = 0;
        this.hasSubGroups = !!(data.groupedBy && data.groupedBy.length);
        this.subGroupDepth = this.hasSubGroups ? data.groupedBy.length : 0;
        this.remaining = this.hasSubGroups ? 0 : data.count - this.data_records.length;

        if (options.hasProgressBar) {
            this.barOptions = {
                columnID: this.db_id,
                progressBarStates: options.progressBarStates,
            };
        }

        this.record_options = _.clone(recordOptions);
        this.record_options.groupReadonlyAttr = this.group_readonly;

        if (options.grouped_by_m2o) {
            // For many2one, a false value means that the field is not set.
            this.title = value ? value : _t('Undefined');
        } else {
            // False and 0 might be valid values for these fields.
            this.title = value === undefined ? _t('Undefined') : value;
        }

        if (options.group_by_tooltip) {
            this.tooltipInfo = _.map(options.group_by_tooltip, function (help, field) {
                return (data.tooltipData && data.tooltipData[field] && "<div>" + help + "<br>" + data.tooltipData[field] + "</div>") || '';
            }).join('');
        } else {
            this.tooltipInfo = "";
        }
    },
    /**
     * @override
     */
    start: function () {
        var self = this;
        var defs = [this._super.apply(this, arguments)];
        this.$header = this.$('.o_kanban_header');

        if (this.hasSubGroups) {
            var $subGroupsContainer = $('<div/>', { class: 'o_kanban_subgroups' });
            var $loadMore = this.$('.o_kanban_load_more');
            if ($loadMore.length) {
                $loadMore.before($subGroupsContainer);
            } else {
                $subGroupsContainer.insertAfter(this.$header);
            }
            this._addSubGroups(this.data_records, $subGroupsContainer, 1, defs);
        } else {
            for (var i = 0; i < this.data_records.length; i++) {
                var def = this._addRecord(this.data_records[i]);
                if (def.state() === 'pending') {
                    defs.push(def);
                }
            }
        }
        this.$header.find('.o_kanban_header_title').tooltip();
        this.$el.css('--o-kanban-main-header-offset', (this.$header.outerHeight() || 44) + 'px');
        this.$el.toggleClass('o_kanban_group_has_subgroups', this.hasSubGroups);
        if (this.hasSubGroups) {
            this.$el.addClass('o_kanban_group_subgroup_depth_' + Math.min(this.subGroupDepth, 3));
        }

        // Multidados:
        // Adiicona verificação do campo draggable para indicar se os registros
        // devem poder ser arrastados
        if (!config.device.isMobile && this.draggable && !this.hasSubGroups) {

            // deactivate sortable in mobile mode.  It does not work anyway,
            // and it breaks horizontal scrolling in kanban views.  Someday, we
            // should find a way to use the touch events to make sortable work.
            this.$el.sortable({
                connectWith: '.o_kanban_group',
                containment: this.draggable ? false : 'parent',
                revert: 0,
                delay: 0,
                items: '> .o_kanban_record:not(.o_updating).draggable',
                cursor: 'move',
                over: function () {
                    self.$el.addClass('o_kanban_hover');
                },
                out: function () {
                    self.$el.removeClass('o_kanban_hover');
                },
                start: function (event, ui) {
                    ui.item.addClass('o_currently_dragged');
                },
                stop: function (event, ui) {
                    var item = ui.item;
                    setTimeout(function () {
                        item.removeClass('o_currently_dragged');
                    });
                },
                update: function (event, ui) {
                    var record = ui.item.data('record');
                    var index = self.records.indexOf(record);
                    record.$el.removeAttr('style');  // jqueryui sortable add display:block inline
                    if (index >= 0) {
                        if ($.contains(self.$el[0], record.$el[0])) {
                            // resequencing records
                            self.trigger_up('kanban_column_resequence', {ids: self._getIDs()});
                        }
                    } else {
                        // adding record to this column
                        ui.item.addClass('o_updating');
                        self.trigger_up('kanban_column_add_record', {record: record, ids: self._getIDs()});
                    }
                }
            });
        }
        this.$el.click(function (event) {
            if (self.folded) {
                self._onToggleFold(event);
            }
        });
        if (this.barOptions) {
            this.$el.addClass('o_kanban_has_progressbar');
            this.progressBar = new KanbanColumnProgressBar(this, this.barOptions, this.data);
            defs.push(this.progressBar.appendTo(this.$header));
        }

        var title = this.folded ? this.title + ' (' + this.data.count + ')' : this.title;
        this.$header.find('.o_column_title').text(title);

        this.$el.toggleClass('o_column_folded', this.folded && !config.device.isMobile);
        var tooltip = this.data.count + _t(' records');
        tooltip = '<p>' + tooltip + '</p>' + this.tooltipInfo;
        this.$header.find('.o_kanban_header_title').tooltip({}).attr('data-original-title', tooltip);
        if (!this.remaining) {
            this.$('.o_kanban_load_more').remove();
        } else {
            this.$('.o_kanban_load_more').html(QWeb.render('KanbanView.LoadMore', {widget: this}));
        }

        return $.when.apply($, defs);
    },
    /**
     * Called when a record has been quick created, as a new column is rendered
     * and appended into a fragment, before replacing the old column in the DOM.
     * When this happens, the quick create widget is inserted into the new
     * column directly, and it should be focused. However, as it is rendered
     * into a fragment, the focus has to be set manually once in the DOM.
     */
    on_attach_callback: function () {
        _.invoke(this.records, 'on_attach_callback');
        if (this.quickCreateWidget) {
            this.quickCreateWidget.on_attach_callback();
        }
    },

    //--------------------------------------------------------------------------
    // Public
    //--------------------------------------------------------------------------

    /**
     * Adds the quick create record to the top of the column.
     *
     * @returns {Deferred}
     */
    addQuickCreate: function () {
        if (this.folded) {
            // first open the column, and then add the quick create
            this.trigger_up('column_toggle_fold', {
                openQuickCreate: true,
            });
            return;
        }

        if (this.quickCreateWidget) {
            return $.Deferred().reject();
        }
        this.trigger_up('close_quick_create'); // close other quick create widgets
        this.trigger_up('start_quick_create');
        var context = this.data.getContext();
        context['default_' + this.groupedBy] = viewUtils.getGroupValue(this.data, this.groupedBy);
        this.quickCreateWidget = new RecordQuickCreate(this, {
            context: context,
            formViewRef: this.quickCreateView,
            model: this.modelName,
        });
        return this.quickCreateWidget.insertAfter(this.$header);
    },
    /**
     * Closes the quick create widget if it isn't dirty.
     */
    cancelQuickCreate: function () {
        if (this.quickCreateWidget) {
            this.quickCreateWidget.cancel();
        }
    },
    /**
     * @returns {Boolean} true iff the column is empty
     */
    isEmpty: function () {
        return !this.records.length;
    },

    //--------------------------------------------------------------------------
    // Private
    //--------------------------------------------------------------------------

    /**
     * Adds a record in the column.
     *
     * @private
     * @param {Object} recordState
     * @param {Object} [options]
     * @param {jQuery} [options.container]
     * @param {string} [options.position]
     *        'before' to add at the top, add at the bottom by default
     * @return {Deferred}
     */
    _addRecord: function (recordState, options) {
        if (!_.isObject(recordState) || (recordState.type && recordState.type !== 'record')) {
            return $.when();
        }
        var record = new this.KanbanRecord(this, recordState, this.record_options);
        this.records.push(record);
        if (options && options.container) {
            return record.appendTo(options.container);
        }
        if (options && options.position === 'before') {
            return record.insertAfter(this.quickCreateWidget ? this.quickCreateWidget.$el : this.$header);
        } else {
            var $load_more = this.$('.o_kanban_load_more');
            if ($load_more.length) {
                return record.insertBefore($load_more);
            } else {
                return record.appendTo(this.$el);
            }
        }
    },
    /**
     * Renders nested groups in the current column.
     *
     * @private
     * @param {Object[]} groups
     * @param {jQuery} $container
     * @param {number} level
     * @param {Deferred[]} defs
      *
      * PT-BR:
      * Monta recursivamente a hierarquia de subgrupos e records dentro da
      * coluna atual, respeitando o estado aberto/fechado retornado pelo model.
     */
    _addSubGroups: function (groups, $container, level, defs) {
        var self = this;
        if (this.showLevelTotals && level === 1 && groups.length) {
            var levelTotal = _.reduce(groups, function (acc, groupState) {
                return acc + self._countGroupCards(groupState);
            }, 0);
            $('<div/>', {
                class: 'o_kanban_subgroup_level_total o_kanban_subgroup_level_total_' + level,
                text: _t('Level') + ' ' + level + ': ' + levelTotal + ' ' + _t('cards'),
            }).appendTo($container);
        }
        var collapseByDefault = groups.length > 1;
        _.each(groups, function (groupState, groupIndex) {
            if (!groupState) {
                return;
            }
            var startCollapsed;
            if (groupState.isOpen !== undefined) {
                startCollapsed = !groupState.isOpen;
            } else {
                startCollapsed = collapseByDefault;
                if (groups.length > 3 && groupIndex === 0) {
                    startCollapsed = false;
                }
            }
            var toneStep = 0;
            if (groups.length > 1) {
                toneStep = Math.round((groupIndex / (groups.length - 1)) * 7);
            }
            var title = groupState.value;
            if (title === undefined || title === null || title === false) {
                title = _t('Undefined');
            }

            var $group = $('<div/>', {
                class: 'o_kanban_subgroup o_kanban_subgroup_level_' + level +
                    ' o_kanban_subgroup_tone_' + toneStep +
                    (startCollapsed ? ' o_kanban_subgroup_collapsed' : ''),
                'data-subgroup-id': groupState.id,
                'data-subgroup-count': groupState.count || 0,
            });
            var $header = $('<div/>', {
                class: 'o_kanban_subgroup_header',
                role: 'button',
                tabindex: 0,
                'aria-expanded': startCollapsed ? 'false' : 'true',
            });
            $('<i/>', {
                class: 'o_kanban_subgroup_toggle fa ' + (startCollapsed ? 'fa-chevron-right' : 'fa-chevron-down'),
                role: 'img',
                'aria-label': startCollapsed ? _t('Expand subgroup') : _t('Collapse subgroup'),
            }).appendTo($header);
            $('<span/>', {
                class: 'o_kanban_subgroup_title',
                text: title,
            }).appendTo($header);
            $('<span/>', {
                class: 'o_kanban_subgroup_count',
                text: groupState.count || 0,
            }).appendTo($header);
            $group.append($header);

            var $body = $('<div/>', { class: 'o_kanban_subgroup_body' });
            $group.append($body);
            $container.append($group);

            if (groupState.groupedBy && groupState.groupedBy.length) {
                self._addSubGroups(
                    groupState.data || [],
                    $body,
                    level + 1,
                    defs
                );
                return;
            }

            _.each(groupState.data || [], function (recordState) {
                if (!recordState) {
                    return;
                }
                var def = self._addRecord(recordState, {container: $body});
                if (def.state() === 'pending') {
                    defs.push(def);
                }
            });
        });
    },
    /**
     * Counts records inside one group recursively.
     *
     * @private
     * @param {Object} groupState
     * @returns {number}
      *
      * PT-BR:
      * Calcula total de cards de um grupo, usando `count` quando disponivel
      * e fallback recursivo quando necessario.
     */
    _countGroupCards: function (groupState) {
        var self = this;
        if (!groupState) {
            return 0;
        }
        if (groupState.type === 'record') {
            return 1;
        }
        if (groupState.count !== undefined && groupState.count !== null) {
            return parseInt(groupState.count, 10) || 0;
        }
        return _.reduce(groupState.data || [], function (acc, item) {
            return acc + self._countGroupCards(item);
        }, 0);
    },
    /**
     * Destroys the QuickCreate widget.
     *
     * @private
     */
    _cancelQuickCreate: function () {
        this.quickCreateWidget.destroy();
        this.quickCreateWidget = undefined;
    },
    /**
     * @returns {integer[]} the res_ids of the records in the column
     */
    _getIDs: function () {
        var ids = [];
        this.$('.o_kanban_record').each(function (index, r) {
            ids.push($(r).data('record').id);
        });
        return ids;
    },

    //--------------------------------------------------------------------------
    // Handlers
    //--------------------------------------------------------------------------

    /**
     * @private
     */
    _onAddQuickCreate: function () {
        this.addQuickCreate();
    },
    /**
     * @private
     */
    _onCancelQuickCreate: function () {
        this._cancelQuickCreate();
    },
    /**
     * @private
     * @param {MouseEvent} event
     */
    _onDeleteColumn: function (event) {
        event.preventDefault();
        var buttons = [
            {
                text: _t("Ok"),
                classes: 'btn-primary',
                close: true,
                click: this.trigger_up.bind(this, 'kanban_column_delete'),
            },
            {text: _t("Cancel"), close: true}
        ];
        new Dialog(this, {
            size: 'medium',
            buttons: buttons,
            $content: $('<div>', {
                text: _t("Are you sure that you want to remove this column ?")
            }),
        }).open();
    },
    /**
     * @private
     * @param {MouseEvent} event
     */
    _onEditColumn: function (event) {
        event.preventDefault();
        new view_dialogs.FormViewDialog(this, {
            res_model: this.relation,
            res_id: this.id,
            title: _t("Edit Column"),
            on_saved: this.trigger_up.bind(this, 'reload'),
        }).open();
    },
    /**
     * @private
     * @param {MouseEvent} event
     */
    _onLoadMore: function (event) {
        event.preventDefault();
        this.trigger_up('kanban_load_more');
    },
    /**
     * @private
     * @param {OdooEvent} event
     */
    _onQuickCreateAddRecord: function (event) {
        this.trigger_up('quick_create_record', event.data);
    },
    /**
     * @private
     * @param {MouseEvent} event
     */
    _onToggleFold: function (event) {
        event.preventDefault();
        this.trigger_up('column_toggle_fold');
    },
    /**
     * @private
     * @param {KeyboardEvent} event
     */
    _onSubGroupHeaderKeyDown: function (event) {
        if (event.which === $.ui.keyCode.ENTER || event.which === $.ui.keyCode.SPACE) {
            this._onToggleSubGroup(event);
        }
    },
    /**
     * @private
     * @param {MouseEvent|KeyboardEvent} event
      *
      * PT-BR:
      * Alterna visualmente o subgrupo e, na primeira expansao de um subgrupo
      * ainda nao carregado, dispara carregamento sob demanda para os registros.
     */
    _onToggleSubGroup: function (event) {
        event.preventDefault();
        event.stopPropagation();

        var $header = $(event.currentTarget);
        var $group = $header.closest('.o_kanban_subgroup');
        var $body = $group.children('.o_kanban_subgroup_body');
        var collapsed = $group.toggleClass('o_kanban_subgroup_collapsed').hasClass('o_kanban_subgroup_collapsed');
        $header.attr('aria-expanded', String(!collapsed));
        $header.find('.o_kanban_subgroup_toggle')
            .toggleClass('fa-chevron-down', !collapsed)
            .toggleClass('fa-chevron-right', collapsed)
            .attr('aria-label', collapsed ? _t('Expand subgroup') : _t('Collapse subgroup'));

        var toggledSubgroupId = $group.attr('data-subgroup-id');
        if (toggledSubgroupId) {
            this.trigger_up('kanban_subgroup_toggle', {
                subgroupID: toggledSubgroupId,
                isOpen: !collapsed,
            });
        }

        if (!collapsed &&
                !$group.data('subgroupLoaded') &&
                !$body.children().length &&
                (parseInt($group.attr('data-subgroup-count'), 10) || 0) > 0) {
            var subgroupId = $group.attr('data-subgroup-id');
            if (subgroupId) {
                var $count = $header.find('.o_kanban_subgroup_count');
                var previousCountText = $count.text();
                $group.addClass('o_kanban_subgroup_loading');
                $header.attr('aria-busy', 'true');
                $count.text(_t('Loading...'));
                this.trigger_up('kanban_load_subgroup_records', {
                    subgroupID: subgroupId,
                    columnID: this.db_id,
                    onComplete: function (success) {
                        $group.removeClass('o_kanban_subgroup_loading');
                        $header.removeAttr('aria-busy');
                        if ($count.length) {
                            $count.text(previousCountText);
                        }
                        if (success) {
                            $group.data('subgroupLoaded', true);
                        } else {
                            $group.removeData('subgroupLoaded');
                        }
                    },
                });
            }
        }
    },
    /**
     * @private
     * @param {OdooEvent} ev
     */
    _onTweakColumn: function (ev) {
        ev.data.callback(this.$el);
    },
    /**
     * @private
     * @param {OdooEvent} ev
     */
    _onTweakColumnRecords: function (ev) {
        _.each(this.records, function (record) {
            ev.data.callback(record.$el, record.state.data);
        });
    },
    /**
     * @private
     * @param {MouseEvent} event
     */
    _onArchiveRecords: function (event) {
        event.preventDefault();
        Dialog.confirm(this, _t("Are you sure that you want to archive all the records from this column?"), {
            confirm_callback: this.trigger_up.bind(this, 'kanban_column_records_toggle_active', {
                archive: true,
            }),
        });
    },
    /**
     * @private
     * @param {MouseEvent} event
     */
    _onUnarchiveRecords: function (event) {
        event.preventDefault();
        this.trigger_up('kanban_column_records_toggle_active', {
            archive: false,
        });
    }
});

return KanbanColumn;

});
