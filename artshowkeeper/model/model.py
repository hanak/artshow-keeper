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
import sys
import logging
import random
import decimal
import io
import json
import csv
from datetime import datetime, timedelta
from decimal import Decimal
import math
import base64
from werkzeug.datastructures import FileStorage

from . import session
from . dataset import Dataset
from . item import ItemField, ItemState, ImportedItemField, calculateImportedItemChecksum
from . currency import Currency, CurrencyField

from . summary import SummaryField, DrawerSummaryField, ActorSummary
from . field_value_error import FieldValueError
from artshowkeeper.common.authentication import UserGroups, getNonZeroRandom
from artshowkeeper.common.convert import *
from artshowkeeper.common.result import Result

class Model:
    SESSION_TIMEOUT_HOURS = 2

    def __init__(self, logger, dataset, currency):
        self.__logger = logger
        self.__dataset = dataset
        self.__currency = currency

    def persist(self):
        self.__dataset.persist()
        
    def startNewSession(self, userGroup, userIP):
        """Start a new session.
        Returns:
            Session ID.
        """
        found = False
        while not found:
            sessionID = getNonZeroRandom()
            if not self.findSession(sessionID):
                self.__logger.info('startNewSession: Created a session {0}'.format(sessionID))
                self.__dataset.updateSessionPairs(sessionID, **{
                        session.Field.CREATED_TIMESTAMP: datetime.now(),
                        session.Field.USER_GROUP: userGroup,
                        session.Field.USER_IP: userIP})
                self.renewSession(sessionID)

                self.__dataset.persist()
                return sessionID
            else:
                self.__logger.debug(
                        'startNewSession: Session {0} is already open, trying a different ID.'.format(sessionID))

    def findSession(self, sessionID):
        """Find a session.
        Args:
            sessionID: Session ID.
        Returns:
            True if the session ID is found.
        """
        return sessionID is not None \
            and sessionID != Dataset.GLOBAL_SESSION_ID \
            and self.__dataset.getSessionValue(sessionID, session.Field.CREATED_TIMESTAMP, None) is not None \
            and self.__dataset.getSessionValue(sessionID, session.Field.VALID_UNTIL_TIMESTAMP, None) is not None 


    def sweepSessions(self):
        """Sweep expired sessions.
        """
        now = datetime.now()
        sessionIDs = self.__dataset.getClientSessionIDs()
        for sessionID in sessionIDs:
            validUntilTime = toDateTime(self.__dataset.getSessionValue(sessionID, session.Field.VALID_UNTIL_TIMESTAMP, None))
            if validUntilTime is None:
                self.__dataset.dropValidSession(sessionID)
                self.__logger.debug(
                        'sweepSessions: Session {0} has been dropped because it does not include a valid timestamp.'.format(sessionID))
            elif datetime.now() >= validUntilTime:
                self.__dataset.dropValidSession(sessionID)
                self.__logger.debug(
                        'sweepSessions: Session {0} has been dropped because it has expired (timestamp: %d).'.format(sessionID, validUntilTime))


    def dropSession(self, sessionID):
        """Drop session.
        """
        self.__dataset.dropValidSession(sessionID)


    def renewSession(self, sessionID):
        """Renew session so it does not expire.
        """
        if self.__dataset.getSessionValue(sessionID, session.Field.CREATED_TIMESTAMP, None) is not None:
                self.__dataset.updateSessionPairs(sessionID, **{
                        session.Field.VALID_UNTIL_TIMESTAMP: datetime.now() + timedelta(hours=self.SESSION_TIMEOUT_HOURS)})


    def getSessionUserInfo(self, sessionID):
        """Find session user info.
        Args:
            sessionID: Session ID.
        Returns:
            Dictionary: (userGroup, userIP)
        """
        return self.getSessionUserGroup(sessionID), \
                self.__dataset.getSessionValue(sessionID, session.Field.USER_IP, '')


    def updateSessionUserInfo(self, sessionID, userIP):
        """Update session user info.
        Args:
            sessionID: Session ID.
            userIP: User IP.
        """
        oldIp = self.__dataset.getSessionValue(sessionID, session.Field.USER_IP, None)
        if oldIp is None or oldIp != userIP:
            self.__dataset.updateSessionPairs(sessionID, **{
                    session.Field.USER_IP: userIP})


    def approveDeviceCode(self, sessionID, deviceCode, userGroup):
        """Approve device code for connection."""
        if self.__dataset.getSessionValue(sessionID, session.Field.USER_GROUP, UserGroups.OTHERS) == UserGroups.ADMIN:
            deviceCode = str(deviceCode)
            deviceCodes = self.__dataset.getGlobalDict(session.Field.DEVICE_CODES)
            if deviceCodes.get(deviceCode, None) is None:
                self.__logger.error('approveDeviceCode: Failed approve a device code {0} by session {1} because the device code has been disabled.'.format(deviceCode, sessionID))
                return Result.DISABLED_DEVICE_CODE
            else:
                self.__logger.info('approveDeviceCode: Device code {0} approved by session {1} as [{2}].'.format(deviceCode, sessionID, userGroup))
                deviceCodeInfo = deviceCodes[deviceCode]
                deviceCodeInfo['UserGroup'] = userGroup
                self.__dataset.updateGlobalDict(session.Field.DEVICE_CODES, deviceCodes)
                return Result.SUCCESS
        else:
            self.__logger.error('approveDeviceCode: Request of session {0} to approved device code {1} refused.'.format(sessionID, deviceCode))
            return Result.ACCESS_DENIED


    def generateDeviceCode(self, sessionID):
        """Generate device code for a given session.
        Returns: Device code or None.
        """
        deviceCodes = self.__dataset.getGlobalDict(session.Field.DEVICE_CODES)
        retries = 10
        while retries > 0:
            deviceCode = str(getNonZeroRandom(4))
            if deviceCodes.get(deviceCode, None) is None:
                # drop all codes issued this session
                for usedCode, usedCodeInfo in deviceCodes.items():
                    if usedCodeInfo is not None and usedCodeInfo['SessionID'] == sessionID:
                        deviceCodes[usedCode] = None

                deviceCodes[deviceCode] = { 'SessionID': sessionID }
                if self.__dataset.updateGlobalDict(session.Field.DEVICE_CODES, deviceCodes) \
                        and self.__dataset.updateSessionPairs(sessionID, **{
                                session.Field.DEVICE_CODE: deviceCode}):
                    return deviceCode
                else:
                    return None
            else:
                retries = retries - 1;
        self.__logger.error('generateDeviceCode: Unable to generate device code for a session {0}.'.format(sessionID))
        return None


    def getSessionUserGroup(self, sessionID):
        """Get session group."""
        userGroup = self.__dataset.getSessionValue(sessionID, session.Field.USER_GROUP, UserGroups.UNKNOWN)
        if userGroup == UserGroups.UNKNOWN and self.__dataset.getSessionValue(sessionID, session.Field.DEVICE_CODE, None) is not None:
            deviceCodes = self.__dataset.getGlobalDict(session.Field.DEVICE_CODES)
            deviceCode = self.__dataset.getSessionValue(sessionID, session.Field.DEVICE_CODE, '')
            if deviceCodes.get(deviceCode, None) is None:
                self.__logger.warning('updateSessionUserGroup: Failed to claim device code {0} by session {1} because the device code has been dropped earlier.'.format(deviceCode, sessionID))
            elif deviceCodes[deviceCode].get('SessionID', 0) != sessionID:
                self.__logger.warning('updateSessionUserGroup: Failed to claim device code {0} by session {1} because the device code has been generated for a different session.'.format(deviceCode, sessionID))
            else:
                userGroup = deviceCodes[deviceCode].get('UserGroup', UserGroups.UNKNOWN)
        return userGroup


    def dropDeviceCode(self, sessionID, deviceCode):
        """Disable device code."""
        if self.__dataset.getSessionValue(sessionID, session.Field.USER_GROUP, UserGroups.OTHERS) == UserGroups.ADMIN:
            deviceCode = str(deviceCode)
            deviceCodes = self.__dataset.getGlobalDict(session.Field.DEVICE_CODES)
            if deviceCode in deviceCodes:
                deviceCodes[deviceCode] = None
                self.__dataset.updateGlobalDict(session.Field.DEVICE_CODES, deviceCodes)
                self.__logger.error('dropDeviceCode: Device code {0} dropped.'.format(deviceCode))
            return Result.SUCCESS
        else:
            self.__logger.error('dropDeviceCode: Request of session {0} to drop device code {1} refused.'.format(sessionID, deviceCode))
            return Result.ACCESS_DENIED


    def clearAdded(self, sessionID):
        """Clear the list of added item codes in a session."""
        self.__dataset.updateSessionPairs(sessionID, **{session.Field.ADDED_ITEM_CODES: None})
    
    def getAdded(self, sessionID):        
        """Retrieve a list of item codes of items added in a session."""
        rawAdded = self.__dataset.getSessionValue(sessionID, session.Field.ADDED_ITEM_CODES, None)
        if rawAdded is not None:
            rawAdded = rawAdded.split(',')
            return [code for code in rawAdded if len(code) > 0]
        else:
            return []

    def getAddedItems(self, sessionID):
        """Retrieve a list of items added in a session.
        Returns:
            A list of items added in the session.
        """
        itemCodes = self.getAdded(sessionID)
        items = []
        for itemCode in itemCodes:
            if len(itemCode) > 0:
                item = self.__dataset.getItem(itemCode)
                if item is not None:
                    items.append(item)
                else:
                    self.__logger.error('getAddedItems: Skipping item ''{0}'' because it has not been found.'.format(itemCode))
        return self.__updateItemAmountCurrency(items)
    
    def setSessionValue(self, sessionID, key, value):
        """Set a pair (key, value) in a session."""
        self.__dataset.updateSessionPairs(sessionID, **{key: value})
        
    def clearSessionValue(self, sessionID, key):
        """Remove a (key, value) in a session."""
        self.__dataset.updateSessionPairs(sessionID, **{key: None})        

    def addNewItem(self, sessionID, owner, title, author, medium, amount, charity, note, importNumber=None, requestImportNumberCodeMatch=False):
        """Add a new item.
        Returns:
            Result (class Result).
        """
        ownerRaw = owner
        importNumberRaw = importNumber

        # 1. Evaluate state.
        state = self.__evaluateState(amount, charity)

        # 2. Check validity of the input range.
        owner = toInt(owner)
        if owner is None:
            self.__logger.error('addNewItem: Owner "{0}" is not an integer.'.format(ownerRaw))
            return Result.INPUT_ERROR
        importNumber = toInt(importNumber)
        if importNumberRaw is not None and importNumber is None:
            self.__logger.error('addNewItem: Import Number "{0}" is not an integer.'.format(importNumber))
            return Result.INPUT_ERROR

        if state == ItemState.ON_SALE:
            amount = toDecimal(amount)
            if amount is None or amount < 0:
                self.__logger.error('addNewItem: Amount is not a possitive number.')
                return Result.INPUT_ERROR

            charity = toInt(charity)
            if charity is None or charity < 0 or charity > 100:
                self.__logger.error('addNewItem: Charity is not an integer in a range [0, 100].')
                return Result.INPUT_ERROR
        else:
            amount = None
            charity = None
        
        # 3. Evaluate duplicity.
        if self.__getImportedItem(owner, importNumber) is not None:
            self.__logger.error(
                    'addNewItem: Import item number "{0}" is already defined for owner "{1}".'.format(
                            importNumber, owner))
            return Result.DUPLICATE_IMPORT_NUMBER
        if self.__getSimilarItem(owner, author, title) is not None:
            self.__logger.error('addNewItem: There is a similar item already.')
            return Result.DUPLICATE_ITEM

        # 4. Build a code and insert.
        code = self.__dataset.getNextItemCode(importNumber, importNumber is not None and requestImportNumberCodeMatch)
        if code is None:
            self.__logger.error('addNewItem: Import number {0} cannot be used as code as requested. Item not added.'.format(
                    importNumber))
            return Result.ERROR
        if not self.__dataset.addItem(
                    code=code, owner=owner, title=title, author=author, medium=medium,
                    state=state, initialAmount=amount, charity=charity, note=note,
                    importNumber=importNumber):
            self.__logger.error('addNewItem: Adding item "{0}" failed. Item not added.'.format(code))
            return Result.ERROR

        # 5. Update items added in the session
        addedItemCodes = self.__appendAddedCode(sessionID, code)
        self.__logger.info('addNewItem: Added item "{0}" (added codes: {1})'.format(code, addedItemCodes))

        if importNumber is not None and toInt(code) != importNumber:
            return Result.SUCCESS_BUT_IMPORT_RENUMBERED
        else:
            return Result.SUCCESS

    def __evaluateState(self, amount, charity):
        if amount is not None and charity is not None:
            return ItemState.ON_SALE
        else:
            return ItemState.ON_SHOW

    def __appendAddedCode(self, sessionID, code):
        addedItemCodes = self.getAdded(sessionID)
        if code not in addedItemCodes:
            addedItemCodes.append(code)
            self.__dataset.updateSessionPairs(sessionID, **{session.Field.ADDED_ITEM_CODES: ','.join(addedItemCodes)})
        return ','.join(addedItemCodes)

    def __getImportedItem(self, owner, importNumber):
        item = None
        if owner is not None and importNumber is not None:
            importedItems = self.__dataset.getItems('Owner == "{0}" and ImportNumber == "{1}"'.format(
                    owner, importNumber))
            if len(importedItems) > 0:
                item = importedItems[0]
        return item

    def __getSimilarItem(self, owner, author, title):
        similarItems = self.__dataset.getItems(
                'Owner == "{0}" and Author == "{1}" and Title == "{2}"'.format(
                owner, toQuoteSafeStr(author), toQuoteSafeStr(title)))
        if len(similarItems) > 0:
            return similarItems[0]
        else:
            return None

    def dropImport(self, sessionID):
        """Drop import.
        Args:
            sessionID: Session ID.
        """
        self.__dataset.updateSessionPairs(sessionID, **{
                session.Field.IMPORTED_ITEMS: None,
                session.Field.IMPORTED_CHECKSUM: None})

    def __checkImportedItemConsistency(self, importedItem):
        """Check consistency of imported item.
        Returns:
            Import result.
        """
        if importedItem[ImportedItemField.AUTHOR] is None or len(importedItem[ImportedItemField.AUTHOR]) == 0:
            self.__logger.error('__checkImportedItemConsistency: Author is undefined.')
            return Result.INVALID_AUTHOR
        if importedItem[ImportedItemField.TITLE] is None or len(importedItem[ImportedItemField.TITLE]) == 0:
            self.__logger.error('__checkImportedItemConsistency: Title is undefined.')
            return Result.INVALID_TITLE
        if importedItem[ImportedItemField.INITIAL_AMOUNT] is None and importedItem[ImportedItemField.CHARITY] is None:
            return Result.SUCCESS
        if importedItem[ImportedItemField.INITIAL_AMOUNT] is None or importedItem[ImportedItemField.CHARITY] is None:
            self.__logger.error('__checkImportedItemConsistency: Either charity is undefined while initial amount is defined or vice versa.')
            return Result.INCOMPLETE_SALE_INFO
        if toDecimal(importedItem[ImportedItemField.INITIAL_AMOUNT]) < 0:
            self.__logger.error('__checkImportedItemConsistency: Amount is negative.')
            return Result.INVALID_AMOUNT
        if importedItem[ImportedItemField.CHARITY] < 0 or importedItem[ImportedItemField.CHARITY] > 100:
            self.__logger.error('__checkImportedItemConsistency: Charity is not in a range [0, 100].')
            return Result.INVALID_CHARITY
        return Result.SUCCESS

    def __calculateImportItemsChecksum(self, importedItems):
        """Calculate checksum of the import.
        Returns:
            Imported item with the result inside.
        """
        checksum = 0
        for item in importedItems:
            checksum = checksum ^ calculateImportedItemChecksum(item)
        return checksum

    def __matchStrings(self, stringA, stringB):
        """Match strings, case insensitive.
        Return:
            True if string matches.
        """
        return stringA is not None and \
                    stringB is not None and \
                    str(stringA).lower() == str(stringB).lower()

    def __matchImportedItems(self, itemA, itemB):
        """Match imported items
        Returns:
            True if the items matches.
        """
        return itemA is not None and \
                itemB is not None and \
                itemA[ImportedItemField.IMPORT_RESULT] == Result.SUCCESS and \
                itemB[ImportedItemField.IMPORT_RESULT] == Result.SUCCESS and \
                (
                    (
                        self.__matchStrings(itemA[ImportedItemField.AUTHOR], itemB[ImportedItemField.AUTHOR]) and \
                        self.__matchStrings(itemA[ImportedItemField.TITLE], itemB[ImportedItemField.TITLE]) \
                    ) or ( \
                        self.__matchStrings(itemA[ImportedItemField.NUMBER], itemB[ImportedItemField.NUMBER]) \
                    ) \
                )

    def __checkDuplicityWithinImport(self, importedItems):
        """Check whether there are duplicities within the import items."""
        resultUpdate = {}
        for i in range(0, len(importedItems) - 1):
            item = importedItems[i]
            if item[ImportedItemField.IMPORT_RESULT] == Result.SUCCESS:
                for j in range(i + 1, len(importedItems)):
                    otherItem = importedItems[j]
                    if self.__matchImportedItems(item, otherItem):
                        self.__logger.info('__checkImportedItemsDuplicity: Item {0} is duplicate of an item {1}.'.format(
                                json.dumps(otherItem, cls=JSONDecimalEncoder), json.dumps(item, cls=JSONDecimalEncoder)))
                        resultUpdate[i] = Result.DUPLICATE_ITEM
                        resultUpdate[j] = Result.DUPLICATE_ITEM

        for index, result in resultUpdate.items():
            importedItems[index][ImportedItemField.IMPORT_RESULT] = result


    def __postProcessImport(self, sessionID, importedItems):
        # 1. Remove previous data (if any).
        self.dropImport(sessionID)

        # 2. Calculate checksum
        importedItemsChecksum = self.__calculateImportItemsChecksum(importedItems)

        # 3. Check for duplicites
        self.__checkDuplicityWithinImport(importedItems)

        # 4. Update session.
        self.__dataset.updateSessionPairs(sessionID, **{
                session.Field.IMPORTED_ITEMS: json.dumps(importedItems),
                session.Field.IMPORTED_CHECKSUM: importedItemsChecksum})

        return importedItemsChecksum

    def isOwnerDefinedInImport(self, importedItems):
        """
        Returns:
            true if the owner is defined
        """
        ownerDefined = True
        for importedItem in importedItems:
            if importedItem[ImportedItemField.OWNER] is None \
                    or importedItem[ImportedItemField.OWNER] == '':
                ownerDefined = False
                break
        return ownerDefined

    def importCSVFile(self, sessionID, stream, headerRow=True, encoding='utf-8'):
        """Import from a CSV file.
        Args:
            sessionID -- Session ID.
            stream -- File stream.
            headerRow -- True if the first row is the header
            encoding -- Encoding of the file.
        Returns:
            imported items, checksum
        """

        # 1. Import stream.
        importedItems = []
        textReader = io.TextIOWrapper(buffer=stream, encoding=encoding, errors='replace')
        try:
            lineIndex = 0
            done = False
            csvReader = csv.reader(textReader)
            for row in csvReader:
                if not headerRow or lineIndex > 0:
                    importedItems.append(self.__procesItemImport(self.__mapCSVRowToImport(row)))
                lineIndex = lineIndex + 1
        finally:
            textReader.detach()

        # 2. Postprocess data.
        importedItemsChecksum = self.__postProcessImport(sessionID, importedItems)

        self.__logger.info('importFile: Found {0} item(s) with a checksum "{1}".'.format(
                len(importedItems), importedItemsChecksum))

        return importedItems, importedItemsChecksum

    def __mapCSVRowToImport(self, row):
        """Create a dictionary containing components of the input row."""
        rowValueOrder = [
                ImportedItemField.NUMBER,
                ImportedItemField.OWNER,
                ImportedItemField.AUTHOR,
                ImportedItemField.TITLE,
                ImportedItemField.MEDIUM,
                ImportedItemField.NOTE,
                ImportedItemField.INITIAL_AMOUNT,
                ImportedItemField.CHARITY]
        mappedRow = {}
        i = 0
        while i < len(row) and i < len(rowValueOrder):
            mappedRow[rowValueOrder[i]] = row[i]
            i = i + 1
        return mappedRow

    def __procesItemImport(self, rawItemImport):
        """Proces item import.
        Returns:
            Imported item with the result inside.
        """
        result, item = self.__dataset.normalizeItemImport(rawItemImport)
        if result != Result.SUCCESS:
            self.__logger.error('__importRawItem: Reading a raw item "{0}" failed with a result {1}.'.format(
                    json.dumps(rawItemImport, cls=JSONDecimalEncoder), result))
        else:
            result = self.__checkImportedItemConsistency(item)
            if result != Result.SUCCESS:
                self.__logger.error('__importRawItem: Line "{0}" failed consistency check with a result {1}.'.format(
                        json.dumps(rawItemImport, cls=JSONDecimalEncoder), result))

        item[ImportedItemField.IMPORT_RESULT] = result
        return item

    def __extractTaggedValue(self, line, tags):
        """Extracts tagged value.
        Returns:
            (tag field, tag value) or (None, None) if the line does not contain a tag.
        """
        if line is None or len(line) == 0:
            return None, None

        index = 0
        while index < len(line) and not line[index].isalnum():
            index = index + 1
        line = line[index:]

        if len(line) == 0:
            return None, None

        for tag, field in tags.items():
            if line.startswith(tag):
                valueIndex = line.find(':')
                if valueIndex < 0:
                    return field, ''
                else:
                    return field, line[(valueIndex + 1):].strip(' \t\r\n')

        return None, None

    def importText(self, sessionID, text):
        """Import from text."""

        # 1. Import stream.
        firstTag = 'A)'
        tags = {
                'A)': ImportedItemField.NUMBER,
                'B)': ImportedItemField.AUTHOR,
                'C)': ImportedItemField.TITLE,
                'D)': ImportedItemField.INITIAL_AMOUNT,
                'E)': ImportedItemField.CHARITY }

        importedItems = []
        rawItem = {}
        textStream = io.StringIO(initial_value=text)
        for line in textStream:
            tagField, tagValue = self.__extractTaggedValue(line, tags)
            if tagValue is not None:            
                if tagField == tags[firstTag]:
                    if len(rawItem) != 0:
                        importedItems.append(self.__procesItemImport(rawItem))
                        rawItem.clear()
                rawItem[tagField] = tagValue

        if len(rawItem) != 0:
            importedItems.append(self.__procesItemImport(rawItem))

        # 2. Postprocess data.
        importedItemsChecksum = self.__postProcessImport(sessionID, importedItems)

        self.__logger.info('importText: Found {0} item(s) with a checksum "{1}".'.format(
                len(importedItems), importedItemsChecksum))

        return importedItems, importedItemsChecksum

    def applyImport(self, sessionID, checksum, defaultOwner):
        """Apply items from an item.
        Items which did not import well are skipped.
        Args:
            sessionID -- Session ID.
            checksum -- Import checsum.
            defaultOwner -- Owner in case owner is not defined.
        Returns:
            (result, skipped items, renumbered items).
        """
        # 1. Check validity of the input
        importedChecksum = self.__dataset.getSessionValue(sessionID, session.Field.IMPORTED_CHECKSUM, None)
        importedItemsRaw = self.__dataset.getSessionValue(sessionID, session.Field.IMPORTED_ITEMS)
        if importedChecksum is None or importedItemsRaw is None:
            self.__logger.debug('applyImport: There is no import to apply.')
            return Result.NO_IMPORT, [], []

        checksumRaw = checksum
        checksum = toInt(checksum)
        if checksum is None or importedChecksum != str(checksum):
            self.__logger.debug('applyImport: Checksum "{0}" does not match stored checksum "{1}".'.format(checksumRaw, importedChecksum))
            return Result.INVALID_CHECKSUM, [], []

        defaultOwnerRaw = defaultOwner
        defaultOwner = toInt(defaultOwner)
        if defaultOwner is not None and defaultOwner is None:
            self.__logger.error('applyImport: Default owner "{0}" is not an integer.'.format(defaultOwnerRaw))
            return Result.INPUT_ERROR, [], []

        importedItems = []
        try:
            importedItems = json.loads(importedItemsRaw)
        except ValueError as err:
            self.__logger.error('applyImport: Imported items [{0}] are corrupted. Decoding failed with an error {1}.'.format(
                    importedItemsRaw, str(err)))
            return Result.INPUT_ERROR, [], []

        # 2. Add items
        skippedItems = []
        renumberedItems = []

        # 2a. Filter items and complete defaults
        for i in range(0, len(importedItems)):
            item = importedItems[i]
            if item[ImportedItemField.IMPORT_RESULT] != Result.SUCCESS:
                skippedItems.append(item)
                importedItems[i] = None
            elif item[ImportedItemField.OWNER] is None:
                item[ImportedItemField.OWNER] = defaultOwner

        # 2b. Order items by import number in order to prevent renumbers if not required.
        importedItems.sort(key=lambda item: item[ImportedItemField.NUMBER] if item is not None and item[ImportedItemField.NUMBER] is not None else -1)

        # 2c. Add new items which might does not need renumbering
        for i in range(0, len(importedItems)):
            item = importedItems[i]
            if item is not None and item[ImportedItemField.NUMBER] is not None:
                addResult = self.addNewItem(
                        sessionID,
                        owner=item[ImportedItemField.OWNER],
                        title=item[ImportedItemField.TITLE],
                        author=item[ImportedItemField.AUTHOR],
                        medium=item[ImportedItemField.MEDIUM],
                        amount=item[ImportedItemField.INITIAL_AMOUNT],
                        charity=item[ImportedItemField.CHARITY],
                        note=item[ImportedItemField.NOTE],
                        importNumber=item[ImportedItemField.NUMBER],
                        requestImportNumberCodeMatch=True)
                if addResult == Result.SUCCESS:
                    self.__logger.debug('applyImport: Item {0} has been processed.'.format(
                            json.dumps(item, cls=JSONDecimalEncoder)))
                    importedItems[i] = None

        # 2d. Add the rest.
        for item in importedItems:
            if item is not None:
                addResult = self.addNewItem(
                        sessionID,
                        owner=item[ImportedItemField.OWNER],
                        title=item[ImportedItemField.TITLE],
                        author=item[ImportedItemField.AUTHOR],
                        medium=item[ImportedItemField.MEDIUM],
                        amount=item[ImportedItemField.INITIAL_AMOUNT],
                        charity=item[ImportedItemField.CHARITY],
                        note=item[ImportedItemField.NOTE],
                        importNumber=item[ImportedItemField.NUMBER])

                if addResult == Result.DUPLICATE_IMPORT_NUMBER:
                    addResult = self.__updateImportedItem(
                        sessionID,
                        owner=item[ImportedItemField.OWNER],
                        importNumber=item[ImportedItemField.NUMBER],
                        title=item[ImportedItemField.TITLE],
                        author=item[ImportedItemField.AUTHOR],
                        medium=item[ImportedItemField.MEDIUM],
                        amount=item[ImportedItemField.INITIAL_AMOUNT],
                        charity=item[ImportedItemField.CHARITY],
                        note=item[ImportedItemField.NOTE])

                item[ImportedItemField.IMPORT_RESULT] = addResult

                if item[ImportedItemField.IMPORT_RESULT] in [Result.SUCCESS, Result.NOTHING_TO_UPDATE]:
                    self.__logger.debug('applyImport: Item {0} has been processed.'.format(
                            json.dumps(item, cls=JSONDecimalEncoder)))
                elif item[ImportedItemField.IMPORT_RESULT] == Result.SUCCESS_BUT_IMPORT_RENUMBERED:
                    self.__logger.debug('applyImport: Item {0} has been processed with renumbering.'.format(
                            json.dumps(item, cls=JSONDecimalEncoder)))
                    renumberedItems.append(item)
                else:
                    self.__logger.error('applyImport: Importing item {0} failed with an error {1}.'.format(
                            json.dumps(item, cls=JSONDecimalEncoder), addResult))
                    skippedItems.append(item)

        self.__logger.info('applyImport: Added {0} item(s). Skipped {1} item(s).'.format(
                len(self.getAdded(sessionID)), len(skippedItems)))

        # 3. Drop import
        self.dropImport(sessionID)

        return Result.SUCCESS, skippedItems, renumberedItems

    def __diffAndUpdateItem(self, itemDiff, fieldName, item, valueNew, valueRaw, required):
        """Diffs a given field of the current item and a new value and updates item.
        Raises:
            FieldValueError in a case of an error.
        """
        if valueNew is not None:
            if item[fieldName] != valueNew:
                valueOld = item[fieldName]
                item[fieldName] = valueNew
                itemDiff[fieldName] = valueNew
                self.__logger.debug(
                        '__diffAndUpdateItem: Field "{0}" will be updated from "{1}" to "{2}".'.format(
                                fieldName, valueOld, valueNew))
            else:
                self.__logger.debug('__diffAndUpdateItem: Field "{0}" not updated because it is the same.'.format(fieldName))

        elif valueRaw is None or valueRaw == '':
            if required:
                raise FieldValueError(fieldName, valueRaw)
            elif item[fieldName] is not None and item[fieldName] != '':
                item[fieldName] = None
                itemDiff[fieldName] = None

        else:
            raise FieldValueError(fieldName, valueRaw)

    def __checkDataConsistency(self, item):
        """Check logical consistency of an item.
        Returns:
            Result (class Result).
        """
        # Items which were not sold.
        if item[ItemField.STATE] in [ItemState.OPEN, ItemState.ON_SHOW]:
            # Item which does not require sale data.
            return Result.SUCCESS
        if item[ItemField.STATE] == ItemState.FINISHED:
            if item[ItemField.INITIAL_AMOUNT] is None and item[ItemField.CHARITY] is None:
                # Finished unsold or on-show item.
                return Result.SUCCESS

        # Items which might be sold.
        if item[ItemField.INITIAL_AMOUNT] is None:
            self.__logger.error('__checkItemConsistency: Item "{0}" is not consistent because initial amount is not defined.'.format(
                    item[ItemField.CODE]))
            return Result.INITIAL_AMOUNT_NOT_DEFINED
        if item[ItemField.CHARITY] is None:
            self.__logger.error('__checkItemConsistency: Item "{0}" is not consistent because charity is not defined.'.format(
                    item[ItemField.CODE]))
            return Result.CHARITY_NOT_DEFINED
        if item[ItemField.STATE] in [ItemState.ON_SALE, ItemState.NOT_SOLD]:
            # Item offered to be sold.
            return Result.SUCCESS

        # Items which were sold.
        if item[ItemField.AMOUNT] is None:
            self.__logger.error('__checkItemConsistency: Item "{0}" is not consistent because amount is not defined.'.format(
                    item[ItemField.CODE]))
            return Result.AMOUNT_NOT_DEFINED
        if item[ItemField.BUYER] is None:
            self.__logger.error('__checkItemConsistency: Item "{0}" is not consistent because buyer is not defined.'.format(
                    item[ItemField.CODE]))
            return Result.BUYER_NOT_DEFINED
        if item[ItemField.AMOUNT] < item[ItemField.INITIAL_AMOUNT]:
            self.__logger.error('__checkItemConsistency: Item "{0}" is not consistent because amount ({1}) is smaller than initial amount ({2}).'.format(
                    item[ItemField.CODE], item[ItemField.AMOUNT], item[ItemField.INITIAL_AMOUNT]))
            return Result.AMOUNT_TOO_LOW

        return Result.SUCCESS

    def __updateImportedItem(self, sessionID, owner, importNumber, title, author, medium, amount, charity, note):
        """Update item based on the pair (owner, importNumber).
        Returns:
            Result code (class Result).
        """
        item = self.__getImportedItem(owner, importNumber)
        if item is None:
            self.__logger.error(
                    'updateImportedItem: Import number "{0}" of owner "{1}" not fount.'.format(
                    importNumber, owner))
            return Result.ITEM_NOT_FOUND

        if item[ItemField.STATE] in ItemState.AMOUNT_SENSITIVE:
            self.__logger.error(
                    'updateImportedItem: Import number "{0}" of owner "{1}" is already closed.'.format(
                    importNumber, owner))
            return Result.ITEM_CLOSED_ALREADY

        updateResult = self.updateItem(
                itemCode=item[ItemField.CODE],
                owner=item[ItemField.OWNER],
                title=title,
                author=author,
                medium=medium,
                initialAmount=amount,
                charity=charity,
                note=note,
                state=self.__evaluateState(amount, charity),
                amount=item[ItemField.AMOUNT],
                buyer=item[ItemField.BUYER])

        if updateResult == Result.SUCCESS:
            addedItemCodes = self.__appendAddedCode(sessionID, item[ItemField.CODE])
            self.__logger.info('__updateImportedItem: Updated item "{0}" (added codes: {1})'.format(item[ItemField.CODE], addedItemCodes))

        return updateResult

    def updateItem(self, itemCode, owner, title, author, medium, state, initialAmount, charity, amount, buyer, note):
        # 1. Get the original item.
        item = self.getItem(itemCode)
        if item is None:
            self.__logger.error('updateItem: Item "{0}" not fount'.format(itemCode))
            return Result.ITEM_NOT_FOUND

        # 2. Build list of compoments to be updated and check value ranges.
        itemDiff = {}
        try:
            self.__diffAndUpdateItem(
                    itemDiff, ItemField.OWNER, item, 
                    checkRange(toInt(owner), 1, None), owner, True)
            self.__diffAndUpdateItem(
                    itemDiff, ItemField.TITLE, item,
                    title, title, False)
            self.__diffAndUpdateItem(
                    itemDiff, ItemField.AUTHOR, item,
                    author, author, False)
            self.__diffAndUpdateItem(
                    itemDiff, ItemField.MEDIUM, item,
                    medium, medium, False)
            self.__diffAndUpdateItem(
                    itemDiff, ItemField.STATE, item,
                    state, state, True)
            self.__diffAndUpdateItem(
                    itemDiff, ItemField.INITIAL_AMOUNT, item,
                    checkRange(toDecimal(initialAmount), 1, None), initialAmount, False)
            self.__diffAndUpdateItem(
                    itemDiff, ItemField.CHARITY, item,
                    checkRange(toDecimal(charity), 0, 100), charity, False)
            self.__diffAndUpdateItem(
                    itemDiff, ItemField.AMOUNT, item,
                    checkRange(toDecimal(amount), 1, None), amount, False)
            self.__diffAndUpdateItem(
                    itemDiff, ItemField.BUYER, item,
                    checkRange(toInt(buyer), 1, None), buyer, False)
            self.__diffAndUpdateItem(
                    itemDiff, ItemField.NOTE, item,
                    note, note, False)
        except FieldValueError as error:
            self.__logger.error('updateItem: Update of an item "{0}" failed due to a value "{1}" of a field {2}'.format(
                    itemCode, error.rawValue, error.name))
            return Result.INVALID_VALUE

        # 3. Check consistency of the result.
        consistencyResult = self.__checkDataConsistency(item)
        if consistencyResult != Result.SUCCESS:
            self.__logger.info('updateItem: Item "{0}" not updated because it is not consistent (consistency result: {1})'.format(itemCode, consistencyResult))
            return consistencyResult

        # 4. Perform an update.
        if len(itemDiff) == 0:
            self.__logger.info('updateItem: Item "{0}" not updated because there is nothing to update'.format(itemCode))
            return Result.NOTHING_TO_UPDATE
        elif self.__dataset.updateItem(itemCode, **itemDiff):
            self.__logger.info('updateItem: Item "{0}" has been updated.'.format(itemCode))
            return Result.SUCCESS
        else:
            self.__logger.error('updateItem: Updating an item "{0}" has failed.'.format(itemCode))
            return Result.ERROR;

    def updateItemImage(self, itemCode, imageBase64):
        item = self.getItem(itemCode)
        if item is None:
            self.__logger.error('updateItemImage: Item "{0}" not found'.format(itemCode))
            return Result.ITEM_NOT_FOUND
        if imageBase64 is None:
            self.__logger.error('updateItemImage: No image data for item "{0}".'.format(itemCode))
            return Result.NO_IMAGE

        SUPPORTED_FORMAT_HEADER = 'data:image/jpeg;base64,'
        if not imageBase64.startswith(SUPPORTED_FORMAT_HEADER):
            self.__logger.error('updateItemImage: Image format of item for item "{0}" is not supported'.format(itemCode))
            return Result.SUPPORTED_FORMAT_HEADER

        return self.__dataset.updateItemJpgImage(
                itemCode,
                base64.decodestring(imageBase64[len(SUPPORTED_FORMAT_HEADER):].encode('ascii')));
                
    def getItemImage(self, itemCode):
        """Provide file containing item image (if present)
        Args:
            itemCode: Item Code.
        Returns:
            (path, filename, timestamp) or (None, None, None) if item is not found or no image is available.
        """
        item = self.getItem(itemCode)
        if item is None:
            self.__logger.error('getItemImage: Item "{0}" not found'.format(itemCode))
            return (None, None, None)
        else:
            return self.__dataset.getItemJpgImage(itemCode)

    def __updateSortCode(self, items):
        """Calculate integer (SORT_CODE) which can be used to sort by code of an item."""
        if items is not None and len(items) > 0:
            for item in items:
                sortCode = 0
                code = item.get(ItemField.CODE, '0')
                if len(code) > 0:
                    if code[0].isalpha():
                        sortCode = (ord(code[0]) * 10000) + int(code[1:])
                    else:
                        sortCode = int(code)
                item[ItemField.SORT_CODE] = sortCode
        return items

    def __calculateAuctionItemIndex(self, items, idealIndex, suppressedAuthor):
        """Calculate index of item suitable for auction around supplied index.
        Args:
            items: Items ordered by AMOUNT ascending.
        Returns:
            Index to the items or None.
        """
        if len(items) < 1:
            return None

        axisLenLeft = idealIndex + 1
        axisLenRight = len(items) - idealIndex + 1

        bestScoreIndex = None
        bestScore = 0
        for index in range(len(items)):
            item = items[index]
            if index > idealIndex:
                x = (index - idealIndex) / axisLenRight
            else:
                x = (idealIndex - index) / axisLenLeft
        
            score = 0.5 * math.cos(x)
            if suppressedAuthor is not None and item[ItemField.AUTHOR] == suppressedAuthor:
                score = score * score

            if score > bestScore:
                bestScore = score
                bestScoreIndex = index

        return bestScoreIndex    

    def __updateAuctionSortCode(self, items):
        """Calculate integer (AUCTION_SORT_CODE) which can be used to sort by code of an item."""
        if items is not None and len(items) > 0:
            index = 0
            for item in items:
                item[ItemField.INDEX] = index
                item[ItemField.AUCTION_SORT_CODE] = 0
                index = index + 1

            itemsToProcess = sorted(items, key=lambda item: item.get(ItemField.AMOUNT, 0))

            # Sequence of indicies at which an item is to be picked.
            indexCoefs = (0, 0.3, 0.6)
            indexCoefsIndex = 0
            lastAuthor = None
            auctionSortCode = 1
            while len(itemsToProcess) > 0:
                idealIndex = int(indexCoefs[indexCoefsIndex] * len(itemsToProcess))
                indexCoefsIndex = (indexCoefsIndex + 1) % len(indexCoefs)

                auctionItemIndex = self.__calculateAuctionItemIndex(itemsToProcess, idealIndex, lastAuthor)
                if auctionItemIndex is None:
                    auctionItemIndex = 0

                item = itemsToProcess[auctionItemIndex]
                items[item[ItemField.INDEX]][ItemField.AUCTION_SORT_CODE] = auctionSortCode
                auctionSortCode = auctionSortCode + 1
                lastAuthor = item[ItemField.AUTHOR]
                del itemsToProcess[auctionItemIndex]

            for item in items:
                del item[ItemField.INDEX]

        return items

    def __updatePermissions(self, items):
        """Updare permissions for each item."""
        if items is not None and len(items) > 0:
            for item in items: 
                printDeleteAllowed = item[ItemField.STATE] in [
                        ItemState.OPEN, ItemState.ON_SHOW, ItemState.ON_SALE]
                item[ItemField.PRINT_ALLOWED] = printDeleteAllowed
                item[ItemField.DELETE_ALLOWED] = printDeleteAllowed
        return items

    def __updateNetAmount(self, items):
        """Update items with net amout and net charity amount for each item."""
        if items is not None and len(items) > 0:
            for item in items:
                if item[ItemField.AMOUNT] is not None:
                    item[ItemField.NET_AMOUNT], item[ItemField.NET_CHARITY_AMOUNT] = self.getItemNetAmount(item)
                else:                    
                    item[ItemField.NET_AMOUNT] = None
                    item[ItemField.NET_CHARITY_AMOUNT] = None
        return items

    def __updateItemAmountCurrency(self, items):
        """Update items with currency specific amount for each item."""
        self.__currency.updateAmountWithAllCurrencies(
                items, {
                        ItemField.INITIAL_AMOUNT: ItemField.INITIAL_AMOUNT_IN_CURRENCY,
                        ItemField.AMOUNT: ItemField.AMOUNT_IN_CURRENCY,
                        ItemField.AMOUNT_IN_AUCTION: ItemField.AMOUNT_IN_AUCTION_IN_CURRENCY })
        return items

    def getAllItems(self):
        return self.__updatePermissions(
                self.__updateSortCode(
                        self.__dataset.getItems(None)))
        
    def getAllClosableItems(self):
        return self.__updateSortCode(
                self.__dataset.getItems('State == "{0}"'.format(
                        ItemState.ON_SALE)))

    def getAllItemsInAuction(self):
        return self.__updateAuctionSortCode(
                self.__updateSortCode(
                            self.__dataset.getItems('State == "{0}"'.format(
                                    ItemState.IN_AUCTION))))

    def getAllPontentiallySoldItems(self):
        rawItems = self.__updateSortCode(
                self.__dataset.getItems('State in [{0}]'.format(toQuotedStr(
                        [ItemState.IN_AUCTION, ItemState.SOLD, ItemState.DELIVERED, ItemState.FINISHED]))))

        # Filter item which have zero amount
        items = []
        for item in rawItems:
            if (item[ItemField.AMOUNT] or 0) > 0 and (item[ItemField.CHARITY] or 0) >= 0:
                items.append(item)
                
        self.__logger.info('getAllPontentiallySoldItems: Retrieved {0} potentially sold items.'.format(len(items)))
                
        return items

    def isItemClosable(self, item):
        return (item is not None) and (ItemField.STATE in item) and (item[ItemField.STATE] == ItemState.ON_SALE)
        
    def isItemDeliverable(self, item):
        return (item is not None) and (ItemField.STATE in item) and (item[ItemField.STATE] in [ItemState.SOLD, ItemState.NOT_SOLD, ItemState.ON_SHOW])
    
    def getNetAmount(self, grossAmount, charityPercent):
        """Calculate a net amount out of a gross amount.
        Args:
            grossAmount: Gross amount.
            charityPercent: 1 = 1%
        Returns:
            A tuple (net sale amount, net charity amount)"""
        try:        
            if grossAmount is None or charityPercent is None:
                self.__logger.error('getItem: Invalid input parameters. Returning zeros.')
                return (Decimal(0), Decimal(0))
            else:
                grossCharity = charityPercent / Decimal(100)

                charityAmount = self.__currency.roundInPrimary(grossAmount * grossCharity)
                netSaleAmount = grossAmount - charityAmount
                return (netSaleAmount, charityAmount)
        except decimal.InvalidOperation:
            self.__logger.exception('getItem: Invalid operation occurred for amount "{0}" and charity "{1}". Returning zeros.'.format(\
                    str(grossAmount), str(grossCharity)))
            return (Decimal(0), Decimal(0))


    def getItemNetAmount(self, item):
        """Calulates the final net amount out of an item.
        Args:
            item: An item.
        Returns:
            A tuple (net sold amount, net charity amount)"""
        ZERO = (Decimal(0), Decimal(0))
        if item is None:
            return ZERO
        elif item[ItemField.STATE] not in [ItemState.IN_AUCTION, ItemState.SOLD, ItemState.DELIVERED, ItemState.FINISHED]:
            return ZERO
        elif ItemField.AMOUNT not in item or ItemField.CHARITY not in item:
            return ZERO
        else:
            return self.getNetAmount(item[ItemField.AMOUNT], item[ItemField.CHARITY])

    def getItemPotentialNetAmount(self, item):
        """Calculates a potential net amount out of an item.
        Args:
            item: An item.
        Returns:
            A tuple (net sold amount, net charity amount)"""
        ZERO = (Decimal(0), Decimal(0))
        if item is None:
            return ZERO
        elif item[ItemField.STATE] not in [ItemState.IN_AUCTION, ItemState.SOLD, ItemState.DELIVERED, ItemState.FINISHED]:
            return ZERO
        elif ItemField.AMOUNT not in item or ItemField.CHARITY not in item:
            return ZERO
        elif item[ItemField.STATE] == ItemState.IN_AUCTION:
            return self.getNetAmount(
                    item[ItemField.AMOUNT_IN_AUCTION] or item[ItemField.AMOUNT] or Decimal(0),
                    item[ItemField.CHARITY] or 0)
        else:
            return self.getNetAmount(item[ItemField.AMOUNT] or Decimal(0), item[ItemField.CHARITY] or 0)

    def getItems(self, itemCodes):
        if len(itemCodes) > 0:
            return self.__updateItemAmountCurrency(
                    self.__updateSortCode(
                            self.__dataset.getItems(
                                    'Code in [{0}]'.format(toQuotedStr(itemCodes)))))
        else:
            return []

    def getAttendees(self):
        return self.__dataset.getAttendees()

    def getAttendee(self, regId):
        return self.__dataset.getAttendee(regId)

    def getItem(self, itemCode):
        if itemCode is None:
            self.__logger.error('getItem: Item code not specified.')
            return None
        else:
            items = self.getItems([itemCode])
            if len(items) > 0:
                return items[0]
            else:
                self.__logger.info('getItem: Item "{0}" not found.'.format(itemCode))
                return None

    def deleteItems(self, itemCodes):
        """Delete item codes.
        Returns:
            Number of deleted items.
        """
        return self.__dataset.items().delete('Code in [{0}]'.format(toQuotedStr(itemCodes)))

    def __validateSaleInput(self, itemCode, item, amount, buyer):
        amount = toDecimal(amount)
        buyer = toInt(buyer)

        if itemCode == None:
            self.__logger.error('__validateSaleInput: Invalid item code.')
            return Result.INVALID_ITEM_CODE            
        if item is None:
            self.__logger.error('__validateSaleInput: Item "{0}" not found.'.format(itemCode))
            return Result.ITEM_NOT_FOUND
        elif not self.isItemClosable(item):
            self.__logger.error('__validateSaleInput: Item "{0}" is not closable.'.format(itemCode))
            return Result.ITEM_NOT_CLOSABLE
        elif buyer is None or buyer <= 0:
            self.__logger.error('__validateSaleInput: Buyer not provided or invalid for item "{0}".'.format(itemCode))
            return Result.INVALID_BUYER
        elif amount is None:
            self.__logger.error('__validateSaleInput: Amount not provided or invalid for item "{0}".'.format(itemCode))
            return Result.INVALID_AMOUNT
        elif amount < item[ItemField.INITIAL_AMOUNT]:
            self.__logger.error(
                    '__validateSaleInput: Amount {0} is too low an item "{1}"'.format(
                            itemCode, amount))
            return Result.AMOUNT_TOO_LOW
        else:
            return Result.SUCCESS
        
    def closeItemAsNotSold(self, itemCode):
        """Close item as sold.
        Returns:
            Result (class Result).
        """
        item = self.getItem(itemCode)
        if item == None:
            self.__logger.error('closeItemAsNotSold: Item "{0}" not found.'.format(itemCode))
            return Result.ITEM_NOT_FOUND
        elif not self.isItemClosable(item):
            self.__logger.error('closeItemAsNotSold: Item "{0}" is not closable.'.format(itemCode))
            return Result.ITEM_NOT_CLOSABLE
        else:
            numUpdated = self.__dataset.updateItem(
                    itemCode,
                    **{
                            ItemField.STATE: ItemState.NOT_SOLD,
                            ItemField.AMOUNT: None,
                            ItemField.BUYER: None})
            if numUpdated != 1:
                self.__logger.error('closeItemAsNotSold: Item "{0}" did not update.'.format(itemCode))
                return Result.ERROR
            else:
                self.__logger.info('closeItemAsNotSold: Item "{0}" set as not sold.'.format(itemCode))
                return Result.SUCCESS
                
    def closeItemAsSold(self, itemCode, amount, buyer):
        item = self.getItem(itemCode)

        result = self.__validateSaleInput(itemCode, item, amount, buyer)
        if result != Result.SUCCESS:
            self.__logger.error(
                    'closeItemAsSold: Closing item "{0}" (amount: {1}, buyer: {2}) failed.'.format(
                            itemCode, buyer, amount))
            return result
        else:
            numUpdated = self.__dataset.updateItem(
                    itemCode,
                    **{
                            ItemField.STATE: ItemState.SOLD,
                            ItemField.AMOUNT: toDecimal(amount),
                            ItemField.BUYER: toInt(buyer)})
            if numUpdated != 1:
                self.__logger.error('closeItemAsSold: Item "{0}" did not update.'.format(itemCode))
                return Result.ERROR
            else:
                self.__logger.info(
                        'closeItemAsSold: Item "{0}" set as sold to {1} for {2}.'.format(
                            itemCode, buyer, amount))
                return Result.SUCCESS
        
    def closeItemIntoAuction(self, itemCode, amount, buyer, imageFile):
        item = self.getItem(itemCode)

        if imageFile is not None:
            result = self.__validateItemImageInput(itemCode, imageFile)
            if result != Result.SUCCESS:
                return result

        result = self.__validateSaleInput(itemCode, item, amount, buyer)
        if result != Result.SUCCESS:
            self.__logger.error('closeItemIntoAuction: Closing item %(code)s (amount: %(amount)s, buyer: %(buyer)s) failed.'
                % { 'code': itemCode, 'buyer': buyer, 'amount': amount })
            return result

        if imageFile is not None:
            result = self.__dataset.updateItemImage(itemCode, imageFile)
            if result != Result.SUCCESS:
                self.__logger.error('closeItemIntoAuction: Updating an image of item %(code)s failed.'
                    % { 'code': itemCode })
                return result

        numUpdated = self.__dataset.updateItem(
                itemCode,
                **{
                        ItemField.STATE: ItemState.IN_AUCTION,
                        ItemField.AMOUNT: toDecimal(amount),
                        ItemField.BUYER: toInt(buyer)})
        if numUpdated != 1:
            self.__logger.error('closeItemIntoAuction: Item ''%(code)s'' did not update.' % { 'code': itemCode })
            return Result.ERROR
        else:
            self.__logger.info('closeItemIntoAuction: Item ''%(code)s'' moved to auction with amount %(amount)s (the last buyer %(buyer)s).'
                % { 'code': itemCode, 'buyer': buyer, 'amount': amount })
            return Result.SUCCESS

    def __convertAmountToCurrencies(self, amount, currencyInfoList):
        """ Convert amount to given currencies.
        Args:
            amount(Decimal)
            currencyInfoList(list of dict[CurrencyField])
        Returns:
            Array of amount in various currencies including formatting info (CurrencyField).
            Primary currency is at index 0.
        """
        if amount is None:
            return []

        currencyInfoList = [currencyInfo.copy() for currencyInfo in currencyInfoList]
        for currencyInfo in currencyInfoList:
            if currencyInfo[CurrencyField.AMOUNT_IN_PRIMARY] > 0:
                try:
                    oneInFixedPoint = Decimal(10) ** currencyInfo[CurrencyField.DECIMAL_PLACES]
                    convertedAmountFixedPoint = (amount * oneInFixedPoint) / currencyInfo[CurrencyField.AMOUNT_IN_PRIMARY];
                    currencyInfo[CurrencyField.AMOUNT] = convertedAmountFixedPoint.quantize(1, rounding=decimal.ROUND_HALF_UP) / oneInFixedPoint
                except decimal.InvalidOperation:
                    self.__logger.exception(
                            '__convertAmountToCurrencies: Amount "{0}" and currency "{1}" caused invalid opreration. Returning zeros.'.format(
                                        str(amount), str(currencyInfo[CurrencyField.CODE])))
                    currencyInfo[CurrencyField.AMOUNT] = Decimal(0)
            else:
                currencyInfo[CurrencyField.AMOUNT] = Decimal(0)
        return currencyInfoList

    def getCurrency(self):
        """ Get currency setup.
        Returns:
            Instance of the active class Currency.
        """
        return self.__currency
     
    def getPotentialCharityAmount(self):
        soldItems = self.getAllPontentiallySoldItems()
        totalCharityAmount = Decimal(0)
        for item in soldItems:
            netAmount, netCharityAmount = self.getItemPotentialNetAmount(item)
            totalCharityAmount = totalCharityAmount + netCharityAmount
        return totalCharityAmount
        
    def getItemInAuction(self):
        itemInAuction = self.__dataset.getItem(self.__dataset.getGlobalValue('ItemCodeInAuction'))
        if itemInAuction is not None and itemInAuction[ItemField.STATE] != ItemState.IN_AUCTION:
            self.__logger.error('getItemInAuction: Item "{0}" is not in auction'.format(itemInAuction[ItemField.CODE]))
            return None
        elif itemInAuction is None:
            return None
        else:
            return self.__updateItemAmountCurrency([itemInAuction])[0]

    def sendItemToAuction(self, itemCode):
        item = self.__dataset.getItem(itemCode)
        if item is None:
            self.__logger.error('sendItemInAuction: Item "{0}" has not been found'.format(itemCode))
            itemToAuction = None
        elif item[ItemField.STATE] != ItemState.IN_AUCTION:
            self.__logger.error('sendItemInAuction: Item "{0}" has incompatible state {1}'.format(itemCode, item[ItemField.STATE]))
            itemToAuction = None
        else:            
            item[ItemField.AMOUNT_IN_AUCTION] = item[ItemField.AMOUNT]
            if not self.__dataset.updateItem(item[ItemField.CODE], **item):
                self.__logger.error('sendItemInAuction: Item "{0}" had not been updated'.format(itemCode))
                itemToAuction = None
            else:
                itemToAuction = item

        if itemToAuction is None:
            self.__dataset.updateGlobalPairs(ItemCodeInAuction=None)
        else:
            self.__dataset.updateGlobalPairs(ItemCodeInAuction=itemCode)

        return itemToAuction

    def updateItemInAuction(self, newAmount):
        item = self.getItemInAuction()
        if item is None:
            self.__logger.error('updateAmountItemInAuction: No valid item in auction')
            return False
        else:
            item[ItemField.AMOUNT_IN_AUCTION] = toDecimal(newAmount)
            if not self.__dataset.updateItem(item[ItemField.CODE], **item):
                self.__logger.error('updateAmountItemInAuction: Item "{0}" had not been updated'.format(item[ItemField.CODE]))
                return False
            else:
                return True
    
    def sellItemInAuction(self, newBuyer):
        newBuyerInt = toInt(newBuyer)
        if newBuyerInt is None:
            self.__logger.error('sellItemInAuction: Buyer "{0}" is not a valid buyer '.format(newBuyer or '<None>'))
            return False
        else:
            item = self.getItemInAuction()
            if item is None:
                self.__logger.error('sellItemInAuction: No valid item in auction')
                return False
            else:
                item[ItemField.STATE] = ItemState.SOLD
                item[ItemField.BUYER] = newBuyer
                item[ItemField.AMOUNT] = item[ItemField.AMOUNT_IN_AUCTION]
                item[ItemField.AMOUNT_IN_AUCTION] = None
                if not self.__dataset.updateItem(item[ItemField.CODE], **item):
                    self.__logger.error('sellItemInAuction: Item "{0}" had not been updated'.format(item[ItemField.CODE]))
                    return False
                else:
                    self.__logger.info('sellItemInAuction: Item "{0}" had been sold to buyer {1} for {2}'.format(item[ItemField.CODE], item[ItemField.BUYER], item[ItemField.AMOUNT]))
                    self.__dataset.updateGlobalPairs(ItemCodeInAuction=None)
                    return True
        
    def sellItemInAuctionNoChange(self):
        item = self.getItemInAuction()
        if item is None:
            self.__logger.error('sellItemInAuctionNoChange: No valid item in auction')
            return False
        else:
            item[ItemField.STATE] = ItemState.SOLD
            item[ItemField.AMOUNT_IN_AUCTION] = None
            if not self.__dataset.updateItem(item[ItemField.CODE], **item):
                self.__logger.error('sellItemInAuctionNoChange: Item "{0}" had not been updated'.format(item[ItemField.CODE]))
                return False
            else:
                self.__logger.info('sellItemInAuctionNoChange: Item "{0}" had been sold to buyer {1} for {2}'.format(item[ItemField.CODE], item[ItemField.BUYER], item[ItemField.AMOUNT]))
                self.__dataset.updateGlobalPairs(ItemCodeInAuction=None)
                return True

    def clearAuction(self):
        item = self.getItemInAuction()
        if item is not None:
            item[ItemField.AMOUNT_IN_AUCTION] = None
            self.__dataset.updateItem(item[ItemField.CODE], **item)
            self.__logger.debug('clearAuction: Item "{0}" has been removed from auction'.format(item[ItemField.CODE]))

        self.__dataset.updateGlobalPairs(ItemCodeInAuction=None)

    def getBadgeReconciliationSummary(self, badge):
        badgeNum = toInt(badge)
        if badgeNum is None:
            self.__logger.error('getBadgeReconciliationSummary: Badge "{0}" is invalid'.format(badge))
            return None
        else:
            availableUnsoldItems = self.__updateSortCode(self.__dataset.getItems(
                    'Owner == "{0}" and State in [{1}]'.format(
                        badgeNum, toQuotedStr([ItemState.ON_SHOW, ItemState.NOT_SOLD]))))

            availableBoughtItems = self.__updateSortCode(self.__dataset.getItems(
                    'Buyer == "{0}" and State == "{1}"'.format(
                        badge, ItemState.SOLD)))
            boughtItemsAmount = Decimal(0)
            for item in availableBoughtItems:
                boughtItemsAmount = boughtItemsAmount + item[ItemField.AMOUNT]

            deliveredSoldItems = self.__updateNetAmount(self.__updateSortCode(self.__dataset.getItems(
                    'Owner == "{0}" and State == "{1}"'.format(
                        badge, ItemState.DELIVERED))))
            charityDeduction = Decimal(0)
            netSaleAmount = Decimal(0)
            for item in deliveredSoldItems:
                itemNetSaleAmount, itemCharityAmount = self.getItemNetAmount(item)
                netSaleAmount = netSaleAmount + itemNetSaleAmount
                charityDeduction = charityDeduction + itemCharityAmount

            pendingSoldItems = self.__updateNetAmount(self.__updateSortCode(self.__dataset.getItems(
                    'Owner == "{0}" and State == "{1}"'.format(
                        badge, ItemState.SOLD))))

            return {
                    SummaryField.AVAILABLE_UNSOLD_ITEMS: availableUnsoldItems,
                    SummaryField.AVAILABLE_BOUGHT_ITEMS: availableBoughtItems,
                    SummaryField.DELIVERED_SOLD_ITEMS: deliveredSoldItems,
                    SummaryField.PENDING_SOLD_ITEMS: pendingSoldItems,
                    SummaryField.GROSS_SALE_AMOUNT: netSaleAmount + charityDeduction,
                    SummaryField.CHARITY_DEDUCTION: charityDeduction,
                    SummaryField.BOUGHT_ITEMS_AMOUNT: boughtItemsAmount,
                    SummaryField.TOTAL_DUE_AMOUNT: boughtItemsAmount - netSaleAmount}

    def reconciliateBadge(self, badge):
        badgeNum = toInt(badge)
        if badgeNum is None:
            self.__logger.error('reconciliateBadge: Badge "{0}" is invalid'.format(badge))
            return False
        else:
            # delivered items first
            self.__dataset.updateMultipleItems(
                    'Owner == "{0}" and State == "{1}"'.format(
                        badge, ItemState.DELIVERED),
                    **{ItemField.STATE: ItemState.FINISHED})

            # bought items second
            self.__dataset.updateMultipleItems(
                    'Buyer == "{0}" and State == "{1}"'.format(
                        badge, ItemState.SOLD),
                    **{ItemField.STATE: ItemState.DELIVERED})

            # unsold items third
            self.__dataset.updateMultipleItems(
                    'Owner == "{0}" and State in [{1}]'.format(
                        badgeNum, toQuotedStr([ItemState.ON_SHOW, ItemState.NOT_SOLD])),
                    **{ItemField.STATE: ItemState.FINISHED})

            return True

    def __getAddActorSummary(self, badge, dict):
        if badge not in dict:
            dict[badge] = ActorSummary(badge)
        return dict[badge]

    def getCashDrawerSummary(self):
        items = self.__dataset.getItems(None)

        totalGrossCashDrawerAmount = 0
        totalNetCharityAmount = 0
        buyersToBeCleared = {}
        ownersToBeCleared = {}
        pendingItems = []
        for item in items:
            if item[ItemField.STATE] == ItemState.FINISHED:
                netSaleAmount, netCharityAmount = self.getItemNetAmount(item)
                totalNetCharityAmount = totalNetCharityAmount + netCharityAmount
                totalGrossCashDrawerAmount = totalGrossCashDrawerAmount + netCharityAmount

            elif item[ItemField.STATE] == ItemState.DELIVERED:
                netSaleAmount, netCharityAmount = self.getItemNetAmount(item)
                totalNetCharityAmount = totalNetCharityAmount + netCharityAmount
                totalGrossCashDrawerAmount = totalGrossCashDrawerAmount + netSaleAmount + netCharityAmount
                self.__getAddActorSummary(item[ItemField.OWNER], ownersToBeCleared).addItemToFinish(netSaleAmount)

            elif item[ItemField.STATE] == ItemState.SOLD:
                self.__getAddActorSummary(item[ItemField.BUYER], buyersToBeCleared).addItemToReceive(item[ItemField.AMOUNT])

            elif item[ItemField.STATE] in [ItemState.ON_SHOW, ItemState.NOT_SOLD]:
                self.__getAddActorSummary(item[ItemField.OWNER], ownersToBeCleared).addItemToFinish(0)

            else:
                pendingItems.append(item)

        return {
                DrawerSummaryField.TOTAL_GROSS_CASH_DRAWER_AMOUNT: totalGrossCashDrawerAmount,
                DrawerSummaryField.TOTAL_NET_CHARITY_AMOUNT: totalNetCharityAmount,
                DrawerSummaryField.TOTAL_NET_AVAILABLE_AMOUNT: totalGrossCashDrawerAmount - totalNetCharityAmount,
                DrawerSummaryField.BUYERS_TO_BE_CLEARED: list(buyersToBeCleared.values()),
                DrawerSummaryField.OWNERS_TO_BE_CLEARED: list(ownersToBeCleared.values()),
                DrawerSummaryField.PENDING_ITEMS: self.__updateSortCode(pendingItems)}


    def __validateItemImageInput(self, itemCode, imageFile):
        if imageFile is None:
            self.__logger.error('__validateItemImageInput: No image supplied for item %(code)s.'
                % { 'code': itemCode })
            return Result.INPUT_ERROR
        elif not isinstance(imageFile, FileStorage):
            self.__logger.error('__validateItemImageInput: Supplied image for item %(code)s is not an image.'
                % { 'code': itemCode })
            return Result.INPUT_ERROR
        elif imageFile.content_type != 'image/jpeg':
            self.__logger.error('__validateItemImageInput: Format "%(content_type)s" of supplied image (item %(code)s) is not supported format.'
                % { 'code': itemCode, 'content_type': imageFile.content_type })
            return Result.UNSUPPORTED_IMAGE_FORMAT
        else:
            return Result.SUCCESS

    def updateItemImage(self, itemCode, imageFile):
        item = self.getItem(itemCode)
        if item is None:
            return Result.ITEM_NOT_FOUND

        result = self.__validateItemImageInput(itemCode, imageFile)
        if result != Result.SUCCESS:
            return result

        result = self.__dataset.updateItemImage(itemCode, imageFile)
        if result != Result.SUCCESS:
            self.__logger.error('updateItemImage: Updating an image of item %(code)s failed.'
                % { 'code': itemCode })
            return result

        return Result.SUCCESS

    def importAttendeeCSVFile(self, sessionID, stream, headerRow=True, encoding='utf-8'):
        """Import attendee from a CSV file.
        Args:
            sessionID -- Session ID.
            stream -- File stream.
            headerRow -- True if the first row is the header
            encoding -- Encoding of the file.
        Returns:
            result
        """

        # 1. Import data.
        importedAttendees = []
        textReader = io.TextIOWrapper(buffer=stream, encoding=encoding, errors='replace')
        try:
            lineIndex = 0
            done = False
            csvReader = csv.reader(textReader)
            for row in csvReader:
                if not headerRow or lineIndex > 0:
                    importedAttendees.append((row[0].strip(), row[1].strip()))
                lineIndex = lineIndex + 1
        finally:
            textReader.detach()

        # 2. Apply data.
        added_count = 0
        for attendee in importedAttendees:
            if self.__dataset.addAttendee(attendee[0], attendee[1]):
                added_count = added_count + 1

        self.__logger.info('importAttendeeCSVFile: Found {0} attendees(s), added/updated {1} attendees.'.format(len(importedAttendees), added_count))

        return Result.SUCCESS

