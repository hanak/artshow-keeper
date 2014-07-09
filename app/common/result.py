# Artshow Keeper: A support tool for keeping an Artshow running.
# Copyright (C) 2014  Ivo Hanak
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
class Result:
    SUCCESS = 'SUCCESS'
    ERROR = 'ERROR'
    CRITICAL_ERROR = 'CRITICAL_ERROR'
    PARTIAL_SUCCESS = 'PARTIAL_SUCCESS'
    INPUT_ERROR = 'INPUT_ERROR'
    DUPLICATE_ITEM = 'DUPLICATE_ITEM'
    INVALID_ITEM_CODE = 'INVALID_ITEM_CODE'
    ITEM_NOT_FOUND = 'ITEM_NOT_FOUND'
    ITEM_NOT_CLOSABLE = 'ITEM_NOT_CLOSABLE'
    NOTHING_TO_PRINT = 'NOTHING_TO_PRINT'
    PRINT_CANCELLED = 'PRINT_CANCELLED'
    NO_ITEMS_SELECTED = 'NO_ITEMS_SELECTED'
    ITEMS_DELETED = 'ITEMS_DELETED'
    NO_ITEM_TO_CLOSE = 'NO_ITEM_TO_CLOSE'
    INVALID_AUTHOR = 'INVALID_AUTHOR'
    INVALID_TITLE = 'INVALID_TITLE'
    INVALID_BUYER = 'INVALID_BUYER'
    INVALID_AMOUNT = 'INVALID_AMOUNT'
    INVALID_CHARITY = 'INVALID_CHARITY'
    INVALID_VALUE = 'INVALID_VALUE'
    INCOMPLETE_SALE_INFO = 'INCOMPLETE_SALE_INFO'
    AMOUNT_TOO_LOW = 'AMOUNT_TOO_LOW'
    NO_ITEM_TO_AUCTION = 'NO_ITEM_TO_AUCTION'
    INVALID_AUCTION_ITEM = 'INVALID_AUCTION_ITEM'
    NEW_AMOUNT_FAILED = 'NEW_AMOUNT_FAILED'
    NEW_BUYER_FAILED = 'NEW_BUYER_FAILED'
    INVALID_BADGE = 'INVALID_BADGE'
    BADGE_ALREADY_RECONCILIATED = 'BADGE_ALREADY_RECONCILIATED'
    RECONCILIATION_DATA_CHANGED = 'RECONCILIATION_DATA_CHANGED'
    BADGE_RECONCILIATION_FAILED = 'BADGE_RECONCILIATION_FAILED'
    BADGE_RECONCILIATION_SUCCEEDED = 'BADGE_RECONCILIATION_SUCCEEDED'
    INITIAL_AMOUNT_NOT_DEFINED = 'INITIAL_AMOUNT_NOT_DEFINED'
    CHARITY_NOT_DEFINED = 'CHARITY_NOT_DEFINED'
    AMOUNT_NOT_DEFINED = 'AMOUNT_NOT_DEFINED'
    BUYER_NOT_DEFINED = 'BUYER_NOT_DEFINED'
    INVALID_FILE = 'INVALID_FILE'
    INVALID_CHECKSUM = 'INVALID_CHECKSUM'
    NO_IMPORT = 'NO_IMPORT'
    CANNOT_AUCTION_THIS_ITEM = 'CANNOT_AUCTION_THIS_ITEM'
    PRIMARY_AMOUNT_IN_PRIMARY_INVALID = 'PRIMARY_AMOUNT_IN_PRIMARY_INVALID'
